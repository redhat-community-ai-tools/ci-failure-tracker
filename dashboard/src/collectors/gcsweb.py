"""
gcsweb HTML Scraper Collector

Scrapes OpenShift CI's gcsweb interface to get Prow test results.
Supports both public (gcsweb-ci) and private (gcsweb-qe-private-deck-ci) instances.
Private instances require an API token via API_KEY environment variable.
"""

import os
import re
import json
import fnmatch
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser

import requests

from .base import BaseCollector, TestResult, JobRun, TestStatus

logger = logging.getLogger(__name__)


class GCSWebLinkParser(HTMLParser):
    """HTML parser to extract directory/file links from gcsweb"""

    def __init__(self):
        super().__init__()
        self.links = []
        self.current_link = None

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            for attr, value in attrs:
                if attr == 'href' and value.startswith('/gcs/'):
                    self.current_link = value

    def handle_data(self, data):
        # Capture link text (build IDs, file names, etc.)
        if self.current_link:
            self.links.append((self.current_link, data.strip()))
            self.current_link = None


class GCSWebCollector(BaseCollector):
    """Collector for gcsweb web interface"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        self.GCSWEB_BASE_URL = config.get('url', 'https://gcsweb-qe-private-deck-ci.apps.ci.l2s4.p1.openshiftapps.com')
        self.BUCKET = config.get('bucket', 'qe-private-deck')

        self.session = requests.Session()
        headers = {'User-Agent': 'CI-Dashboard-Collector/1.0'}

        api_token = config.get('api_token') or os.environ.get('API_KEY')
        if api_token:
            headers['Authorization'] = f'Bearer {api_token}'
            logger.info("[gcsweb] Using API token for authentication")
        else:
            logger.warning("[gcsweb] No API token found - private gcsweb instances will return 403")

        self.session.headers.update(headers)

    @property
    def name(self) -> str:
        return "gcsweb"

    def health_check(self) -> bool:
        """Check if gcsweb is accessible. Sets self.health_error with details on failure."""
        self.health_error = None
        try:
            url = f"{self.GCSWEB_BASE_URL}/gcs/{self.BUCKET}/"
            response = self.session.get(url, timeout=30)
            if response.status_code == 403:
                self.health_error = (
                    "GCSWeb returned HTTP 403 - API token expired or missing. "
                    "Renew at: https://oauth-openshift.apps.ci.l2s4.p1.openshiftapps.com/oauth/token/request "
                    "then set API_KEY environment variable on the deployment."
                )
                return False
            if response.status_code != 200:
                self.health_error = f"GCSWeb returned HTTP {response.status_code} - check URL: {self.GCSWEB_BASE_URL}"
                return False
            return True
        except Exception as e:
            self.health_error = f"Cannot reach GCSWeb: {e}"
            return False

    def _map_status(self, status: str) -> TestStatus:
        """Map Prow status to normalized TestStatus"""
        status_map = {
            'SUCCESS': TestStatus.PASSED,
            'FAILURE': TestStatus.FAILED,
            'ABORTED': TestStatus.ERROR,
            'UNSTABLE': TestStatus.FAILED,
        }
        return status_map.get(status, TestStatus.UNKNOWN)

    def _extract_metadata(self, job_name: str) -> Dict[str, str]:
        """Extract version and platform from job name"""
        metadata = {'version': 'unknown', 'platform': 'unknown'}

        # Extract version from release-X.Y pattern
        version_match = re.search(r'release-(\d+\.\d+)', job_name)
        if version_match:
            metadata['version'] = version_match.group(1)
        else:
            # Check branch_version_map for branch-based jobs (e.g., "main" -> "5.0")
            branch_map = self.config.get('branch_version_map', {})
            for branch, version in branch_map.items():
                if f'-{branch}-' in job_name or job_name.endswith(f'-{branch}'):
                    metadata['version'] = version
                    break

        # Extract platform
        platforms = ['aws', 'gcp', 'azure', 'vsphere', 'nutanix', 'metal', 'ovirt', 'openstack']
        for platform in platforms:
            if platform in job_name.lower():
                metadata['platform'] = platform
                break

        return metadata

    def _list_directory(self, path: str) -> List[tuple]:
        """
        List contents of a directory in gcsweb

        Returns: List of (link_path, link_text) tuples
        """
        url = f"{self.GCSWEB_BASE_URL}{path}"

        try:
            response = self.session.get(url, timeout=120)
            response.raise_for_status()

            # Parse HTML to extract links
            parser = GCSWebLinkParser()
            parser.feed(response.text)

            # Filter out parent directory link (..)
            return [(link, text) for link, text in parser.links if text != '..']

        except Exception as e:
            print(f"Error listing directory {path}: {e}")
            return []

    def _list_job_runs(
        self,
        job_name: str,
        start_date: datetime,
        end_date: datetime,
        max_results: int = 100
    ) -> List[Dict[str, Any]]:
        """
        List job runs for a specific job within date range

        Returns list of run info: [{'job_name': ..., 'build_id': ..., 'path': ...}, ...]
        """
        job_path = f"/gcs/{self.BUCKET}/logs/{job_name}/"
        links = self._list_directory(job_path)

        runs = []
        for link_path, link_text in links:
            # Build IDs are directory names (usually timestamps)
            build_id = link_text.rstrip('/')

            # Try to parse as timestamp
            try:
                if build_id.isdigit() and len(build_id) == 10:
                    # Unix timestamp
                    build_timestamp = datetime.fromtimestamp(int(build_id))
                else:
                    # Try parsing as date format
                    build_timestamp = datetime.strptime(build_id[:10], '%Y-%m-%d') if '-' in build_id else None
            except (ValueError, OSError):
                build_timestamp = None

            # Filter by date if timestamp available
            if build_timestamp:
                if not (start_date <= build_timestamp <= end_date):
                    continue

            runs.append({
                'job_name': job_name,
                'build_id': build_id,
                'path': link_path.rstrip('/'),
                'timestamp': build_timestamp
            })

        # Sort by timestamp (most recent first) and limit
        runs = sorted(runs, key=lambda x: x['timestamp'] or datetime.min, reverse=True)
        return runs[:max_results]

    def _fetch_file(self, path: str) -> Optional[bytes]:
        """Fetch a file from gcsweb"""
        url = f"{self.GCSWEB_BASE_URL}{path}"

        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return response.content
        except Exception as e:
            print(f"Error fetching file {path}: {e}")
            return None

    def _fetch_finished_json(self, run_path: str) -> Optional[Dict[str, Any]]:
        """Fetch finished.json for a job run"""
        content = self._fetch_file(f"{run_path}/finished.json")
        if content:
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return None
        return None

    def _fetch_junit_xml_files(self, run_path: str) -> List[tuple]:
        """Fetch and parse JUnit XML files for a job run (recursive search).
        Returns list of (ET.Element, xml_file_path) tuples."""
        junit_files = []
        self._find_xml_recursive(f"{run_path}/artifacts/", junit_files, depth=0, max_depth=6)
        return junit_files

    def _derive_log_url(self, xml_path: str) -> str:
        """Derive the step-level build-log.txt URL from the JUnit XML path.
        XML at: /gcs/bucket/logs/job/build/artifacts/{step}/.../junit/file.xml
        Log at: {GCSWEB_BASE_URL}/gcs/bucket/logs/job/build/artifacts/{step}/build-log.txt"""
        artifacts_idx = xml_path.find('/artifacts/')
        if artifacts_idx == -1:
            return ''
        after_artifacts = xml_path[artifacts_idx + len('/artifacts/'):]
        step_name = after_artifacts.split('/')[0]
        step_dir = xml_path[:artifacts_idx] + '/artifacts/' + step_name
        return f"{self.GCSWEB_BASE_URL}{step_dir}/build-log.txt"

    def _find_xml_recursive(self, path: str, results: list, depth: int, max_depth: int):
        """Recursively search for XML test result files in artifacts.
        Appends (ET.Element, file_path) tuples to results."""
        if depth >= max_depth:
            return

        links = self._list_directory(path)

        for link_path, link_text in links:
            if link_text.endswith('.xml'):
                content = self._fetch_file(link_path)
                if content:
                    try:
                        root = ET.fromstring(content)
                        if root.tag in ('testsuites', 'testsuite'):
                            results.append((root, link_path))
                            logger.info(f"[gcsweb] Found JUnit XML: {link_text} at depth {depth}")
                    except ET.ParseError:
                        continue
            elif link_text.endswith('/') and link_text not in ('../', './'):
                self._find_xml_recursive(link_path, results, depth + 1, max_depth)

    def _parse_junit_xml(self, junit_root: ET.Element, job_name: str, build_id: str, metadata: Dict[str, str], log_url: str = '') -> List[TestResult]:
        """Parse JUnit XML and extract test results"""
        results = []

        # Find all testcase elements directly to avoid visiting the same
        # testcase multiple times when testsuites are nested.
        for testcase in junit_root.findall('.//testcase'):
            name = testcase.get('name', 'unknown')

            test_filter = self.config.get('test_suite_filter', '')
            if test_filter and test_filter not in name:
                continue

            time = float(testcase.get('time', 0))

            failure = testcase.find('failure')
            error = testcase.find('error')
            skipped = testcase.find('skipped')

            if skipped is not None:
                status = TestStatus.SKIPPED
                error_msg = skipped.get('message')
            elif failure is not None:
                status = TestStatus.FAILED
                error_msg = self._build_error_message(failure, testcase)
            elif error is not None:
                status = TestStatus.ERROR
                error_msg = self._build_error_message(error, testcase)
            else:
                status = TestStatus.PASSED
                error_msg = None

            test_name, test_description = self._extract_test_name(name)

            result = TestResult(
                test_name=test_name,
                status=status,
                timestamp=datetime.now(),
                duration_seconds=time,
                error_message=error_msg,
                job_name=job_name,
                build_id=build_id,
                version=metadata['version'],
                platform=metadata['platform'],
                test_description=test_description,
                job_url=f"https://qe-private-deck-ci.apps.ci.l2s4.p1.openshiftapps.com/view/gs/{self.BUCKET}/logs/{job_name}/{build_id}",
                log_url=log_url or None
            )
            results.append(result)

        return results

    def _build_error_message(self, element: ET.Element, testcase: ET.Element) -> str:
        """Build full error message from JUnit failure/error element and testcase output."""
        parts = []
        msg = element.get('message')
        if msg:
            parts.append(msg)
        if element.text and element.text.strip():
            text = element.text.strip()
            if text != msg:
                parts.append(text)
        system_out = testcase.find('system-out')
        if system_out is not None and system_out.text and system_out.text.strip():
            parts.append('\nTest Output:\n' + system_out.text.strip())
        return '\n'.join(parts) if parts else 'Unknown error'

    def _extract_test_name(self, raw_name: str) -> tuple[str, str]:
        """
        Extract clean test name and description from raw name

        Returns: (test_id, description)
        """
        ocp_match = re.search(r'OCP-\d+', raw_name)

        if ocp_match:
            test_id = ocp_match.group(0)

            # Look for [sig-windows] or similar bracket pattern and extract everything after it
            sig_match = re.search(r'\[sig-[\w-]+\]\s+(.+)', raw_name)
            if sig_match:
                description = sig_match.group(1)
            else:
                # Try other bracket patterns like [wmco]
                bracket_match = re.search(r'\[[\w-]+\]\s+(.+)', raw_name)
                if bracket_match:
                    description = bracket_match.group(1)
                else:
                    # No brackets, extract after OCP ID
                    after_id = raw_name.split(test_id, 1)[-1]
                    description = after_id.strip(':- \t')

            # Remove test suite prefix (if configured)
            test_suite_filter = self.config.get('test_suite_filter', '')
            if test_suite_filter:
                description = re.sub(rf'^{re.escape(test_suite_filter)}[-\s]+', '', description)
            description = re.sub(r'^Smokerun-[^\s]+\s+', '', description)

            # Remove [wmco] or similar prefixes at the start
            description = re.sub(r'^\[[\w-]+\]\s+', '', description)

            # Remove all bracketed tags like [Slow], [Disruptive], [Serial]
            description = re.sub(r'\s*\[[\w-]+\]', '', description)

            return (test_id, description.strip() if description else test_id)

        return (raw_name.strip(), raw_name.strip())

    def _resolve_patterns(self, patterns: List[str]) -> List[str]:
        """
        Resolve wildcard patterns to actual job names by listing the logs directory.
        Exact names (no wildcards) are passed through unchanged.
        """
        exact = []
        wildcards = []
        for p in patterns:
            if '*' in p or '?' in p:
                wildcards.append(p)
            else:
                exact.append(p)

        if not wildcards:
            return exact

        logger.info(f"[gcsweb] Resolving {len(wildcards)} wildcard pattern(s)...")
        logs_path = f"/gcs/{self.BUCKET}/logs/"
        all_jobs = self._list_directory(logs_path)

        matched = set()
        for link_path, link_text in all_jobs:
            job_dir = link_text.rstrip('/')
            for pattern in wildcards:
                if fnmatch.fnmatch(job_dir, pattern):
                    matched.add(job_dir)
                    break

        logger.info(f"[gcsweb] Wildcard patterns matched {len(matched)} job(s)")
        return exact + sorted(matched)

    def collect_job_runs(
        self,
        start_date: datetime,
        end_date: datetime,
        job_patterns: Optional[List[str]] = None,
        versions: Optional[List[str]] = None,
        platforms: Optional[List[str]] = None
    ) -> List[JobRun]:
        """Collect job runs from gcsweb"""

        if not job_patterns:
            raise ValueError("job_patterns is required")

        resolved_jobs = self._resolve_patterns(job_patterns)

        job_runs = []
        max_workers = self.config.get('max_workers', 5)

        # For each job, list recent runs
        for job_name in resolved_jobs:
            runs = self._list_job_runs(job_name, start_date, end_date, max_results=50)

            # Process each run in parallel
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(self._process_job_run, run, versions, platforms): run
                    for run in runs
                }

                for future in as_completed(futures):
                    try:
                        job_run = future.result()
                        if job_run:
                            job_runs.append(job_run)
                    except Exception as e:
                        run = futures[future]
                        print(f"Error processing run {run['build_id']}: {e}")

        return job_runs

    def _process_job_run(
        self,
        run: Dict[str, Any],
        versions: Optional[List[str]] = None,
        platforms: Optional[List[str]] = None
    ) -> Optional[JobRun]:
        """Process a single job run"""

        metadata = self._extract_metadata(run['job_name'])

        # Filter by version/platform
        if versions and metadata['version'] not in versions:
            return None
        if platforms and metadata['platform'] not in platforms:
            return None

        # Fetch finished.json
        finished = self._fetch_finished_json(run['path'])
        if not finished:
            return None

        # Parse timestamps
        timestamp = finished.get('timestamp')
        if timestamp:
            timestamp = datetime.fromtimestamp(timestamp)
        else:
            timestamp = run.get('timestamp') or datetime.now()

        # Fetch JUnit XML to count tests
        junit_files = self._fetch_junit_xml_files(run['path'])

        total_tests = 0
        passed_tests = 0
        failed_tests = 0
        skipped_tests = 0

        for junit_root, _xml_path in junit_files:
            # Collect all testsuite elements, including the root if it is one.
            all_suites = []
            if junit_root.tag == 'testsuite':
                all_suites.append(junit_root)
            all_suites.extend(junit_root.findall('.//testsuite'))

            for testsuite in all_suites:
                # Skip parent testsuites that contain child testsuites to
                # avoid double-counting aggregated counts with children.
                if testsuite.findall('testsuite'):
                    continue
                total_tests += int(testsuite.get('tests', 0))
                failed_tests += int(testsuite.get('failures', 0))
                failed_tests += int(testsuite.get('errors', 0))
                skipped_tests += int(testsuite.get('skipped', 0))

        passed_tests = total_tests - failed_tests - skipped_tests

        # Overall job status
        result = finished.get('result', 'UNKNOWN')
        status = self._map_status(result)

        job_run = JobRun(
            job_name=run['job_name'],
            build_id=run['build_id'],
            status=status,
            timestamp=timestamp,
            duration_seconds=finished.get('duration'),
            version=metadata['version'],
            platform=metadata['platform'],
            total_tests=total_tests,
            passed_tests=passed_tests,
            failed_tests=failed_tests,
            skipped_tests=skipped_tests,
            job_url=f"https://qe-private-deck-ci.apps.ci.l2s4.p1.openshiftapps.com/view/gs/{self.BUCKET}/logs/{run['job_name']}/{run['build_id']}"
        )

        return job_run

    def collect_test_results(
        self,
        start_date: datetime,
        end_date: datetime,
        job_patterns: Optional[List[str]] = None,
        test_names: Optional[List[str]] = None,
        versions: Optional[List[str]] = None,
        platforms: Optional[List[str]] = None
    ) -> List[TestResult]:
        """Collect individual test results from gcsweb"""

        if not job_patterns:
            raise ValueError("job_patterns is required")

        resolved_jobs = self._resolve_patterns(job_patterns)

        all_results = []
        max_workers = self.config.get('max_workers', 5)

        for job_name in resolved_jobs:
            runs = self._list_job_runs(job_name, start_date, end_date, max_results=50)

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(self._process_test_results, run, test_names, versions, platforms): run
                    for run in runs
                }

                for future in as_completed(futures):
                    try:
                        results = future.result()
                        all_results.extend(results)
                    except Exception as e:
                        run = futures[future]
                        print(f"Error processing test results for {run['build_id']}: {e}")

        return all_results

    def _process_test_results(
        self,
        run: Dict[str, Any],
        test_names: Optional[List[str]] = None,
        versions: Optional[List[str]] = None,
        platforms: Optional[List[str]] = None
    ) -> List[TestResult]:
        """Process test results for a single job run"""

        metadata = self._extract_metadata(run['job_name'])

        # Filter by version/platform
        if versions and metadata['version'] not in versions:
            return []
        if platforms and metadata['platform'] not in platforms:
            return []

        # Fetch finished.json for timestamp
        finished = self._fetch_finished_json(run['path'])
        timestamp = run.get('timestamp') or datetime.now()
        if finished and finished.get('timestamp'):
            timestamp = datetime.fromtimestamp(finished['timestamp'])

        # Fetch and parse JUnit XML
        junit_files = self._fetch_junit_xml_files(run['path'])

        all_results = []
        for junit_root, xml_path in junit_files:
            log_url = self._derive_log_url(xml_path)
            results = self._parse_junit_xml(junit_root, run['job_name'], run['build_id'], metadata, log_url=log_url)

            # Update timestamps
            for result in results:
                result.timestamp = timestamp

            # Filter by test name
            if test_names:
                results = [r for r in results if r.test_name in test_names]

            all_results.extend(results)

        return all_results
