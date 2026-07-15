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

    def _strip_rehearse_prefix(self, job_name: str) -> str:
        """Strip rehearse-NNNNN- prefix from rehearse job names.

        Rehearse jobs have names like:
          rehearse-81646-periodic-ci-...-release-4.21-...
        Stripping the prefix lets the existing version regex work.
        """
        match = re.match(r'^rehearse-\d+-(.+)$', job_name)
        if match:
            return match.group(1)
        return job_name

    def _derive_job_type(self, job_name: str) -> str:
        """Derive job type from job name prefix.

        Returns one of: periodic, postsubmit, presubmit, rehearse.
        """
        if job_name.startswith('rehearse-'):
            return 'rehearse'
        if job_name.startswith('branch-ci-'):
            return 'postsubmit'
        if job_name.startswith('pull-ci-'):
            return 'presubmit'
        return 'periodic'

    def _extract_metadata(self, job_name: str) -> Dict[str, str]:
        """Extract version and platform from job name"""
        metadata = {'version': 'unknown', 'platform': 'unknown'}

        # Strip rehearse prefix so existing patterns can match
        effective_name = self._strip_rehearse_prefix(job_name)

        # Extract version from release-X.Y pattern
        version_match = re.search(r'release-(\d+\.\d+)', effective_name)
        if version_match:
            metadata['version'] = version_match.group(1)
        else:
            # Try WMCO FBC postsubmit version format: v10-21, v11-0
            wmco_match = re.search(r'-v(\d+)-(\d+)-', effective_name)
            if wmco_match:
                wmco_major = wmco_match.group(1)
                wmco_minor = wmco_match.group(2)
                wmco_map = self.config.get('wmco_version_map', {})
                ocp_major = wmco_map.get(wmco_major)
                if ocp_major:
                    metadata['version'] = f'{ocp_major}.{wmco_minor}'
                else:
                    # Fallback: WMCO major - 6 = OCP major
                    ocp_major = str(int(wmco_major) - 6)
                    metadata['version'] = f'{ocp_major}.{wmco_minor}'
            else:
                # FBC postsubmit jobs without a version variant use a
                # dedicated config key because their target OCP version
                # may differ from the branch_version_map default.
                fbc_default = self.config.get('fbc_default_version')
                if fbc_default and effective_name.startswith('branch-ci-') and '-fbc-' in effective_name:
                    metadata['version'] = fbc_default
                else:
                    # Check branch_version_map for branch-based jobs (e.g., "main" -> "5.0")
                    branch_map = self.config.get('branch_version_map', {})
                    for branch, version in branch_map.items():
                        if f'-{branch}-' in effective_name or effective_name.endswith(f'-{branch}'):
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
            logger.warning(f"[gcsweb] Error listing directory {path}: {e}")
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

        if not runs:
            logger.warning(f"[gcsweb] No builds found for job: {job_name}")

        return runs[:max_results]

    def _fetch_file(self, path: str) -> Optional[bytes]:
        """Fetch a file from gcsweb"""
        url = f"{self.GCSWEB_BASE_URL}{path}"

        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return response.content
        except Exception as e:
            logger.warning(f"[gcsweb] Error fetching file {path}: {e}")
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

    def _parse_junit_xml(self, junit_root: ET.Element, job_name: str, build_id: str, metadata: Dict[str, str], log_url: str = '', job_url: str = '', job_type: str = None) -> List[TestResult]:
        """Parse JUnit XML and extract test results"""
        results = []

        # Fallback job_url for callers that don't provide one
        if not job_url:
            job_url = (
                f"https://qe-private-deck-ci.apps.ci.l2s4.p1.openshiftapps.com"
                f"/view/gs/{self.BUCKET}/logs/{job_name}/{build_id}"
            )

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
                job_url=job_url,
                log_url=log_url or None,
                job_type=job_type
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

            # Remove any remaining leading separators (: - or spaces)
            description = re.sub(r'^[:\-\s]+', '', description)

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

    def _list_recent_prs(self, repo: str, max_prs: int = 30) -> List[str]:
        """List recent PR numbers under pr-logs/pull/{repo}/.

        Returns PR numbers sorted descending (most recent first), limited
        to *max_prs*.
        """
        path = f"/gcs/{self.BUCKET}/pr-logs/pull/{repo}/"
        links = self._list_directory(path)

        pr_numbers = []
        for _link_path, link_text in links:
            pr_num = link_text.rstrip('/')
            if pr_num.isdigit():
                pr_numbers.append(pr_num)

        # Sort descending (most recent PRs have higher numbers)
        pr_numbers.sort(key=int, reverse=True)
        return pr_numbers[:max_prs]

    def _list_pr_jobs(self, repo: str, pr_number: str, pattern: str) -> List[Dict[str, Any]]:
        """List jobs under a PR directory that match *pattern* (fnmatch).

        Returns list of dicts with job_name, build_id, and path keys --
        same shape as ``_list_job_runs`` output so ``_process_run_single_pass``
        can consume them directly.
        """
        pr_path = f"/gcs/{self.BUCKET}/pr-logs/pull/{repo}/{pr_number}/"
        job_links = self._list_directory(pr_path)

        results = []
        for job_link_path, job_link_text in job_links:
            job_dir = job_link_text.rstrip('/')
            if not fnmatch.fnmatch(job_dir, pattern):
                continue

            # Each job directory contains build-ID sub-directories
            build_links = self._list_directory(job_link_path)
            for build_link_path, build_link_text in build_links:
                build_id = build_link_text.rstrip('/')
                results.append({
                    'job_name': job_dir,
                    'build_id': build_id,
                    'path': build_link_path.rstrip('/'),
                    'timestamp': None,  # will be read from finished.json
                })

        return results

    def _collect_pr_sources(
        self,
        pr_log_sources: List[Dict[str, Any]],
        start_date: datetime,
        end_date: datetime,
        skip_builds: Optional[set] = None,
        versions: Optional[List[str]] = None,
        platforms: Optional[List[str]] = None,
        progress_callback=None
    ) -> tuple:
        """Scan pr-logs/ for rehearse and PR jobs.

        Returns (list[JobRun], list[TestResult]) -- same shape as
        ``collect_all``.
        """
        skip_builds = skip_builds or set()
        all_job_runs = []
        all_test_results = []
        max_workers = self.config.get('max_workers', 5)

        for source in pr_log_sources:
            repo = source.get('repo', '')
            pattern = source.get('job_pattern', '*')
            max_prs = source.get('max_prs', 30)

            logger.info(f"[gcsweb] Scanning PR logs for repo={repo}, pattern={pattern}")

            pr_numbers = self._list_recent_prs(repo, max_prs=max_prs)
            if not pr_numbers:
                logger.info(f"[gcsweb] No PRs found for repo={repo}")
                continue

            logger.info(f"[gcsweb] Found {len(pr_numbers)} PR(s) for {repo}")

            # Collect runs from all PRs
            all_runs = []
            for pr_num in pr_numbers:
                runs = self._list_pr_jobs(repo, pr_num, pattern)
                all_runs.extend(runs)

            # Filter out already-collected builds
            new_runs = [
                r for r in all_runs
                if (r['job_name'], r['build_id']) not in skip_builds
            ]

            logger.info(f"[gcsweb] {repo}: {len(all_runs)} total builds, "
                         f"{len(new_runs)} new")

            if not new_runs:
                continue

            # Process runs in parallel, reusing _process_run_single_pass
            def _process(run):
                return self._process_run_single_pass(
                    run, versions, platforms,
                    start_date=start_date, end_date=end_date
                )

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(_process, run): run for run in new_runs
                }
                for future in as_completed(futures):
                    try:
                        result = future.result()
                        if result:
                            jr, tr = result
                            all_job_runs.append(jr)
                            all_test_results.extend(tr)
                    except Exception as e:
                        run = futures[future]
                        logger.warning(
                            f"[gcsweb] Error processing PR build "
                            f"{run['job_name']}/{run['build_id']}: {e}"
                        )

            if progress_callback:
                progress_callback(
                    f'PR logs ({repo}): {len(all_job_runs)} builds collected'
                )

        logger.info(
            f"[gcsweb] PR log scan complete: {len(all_job_runs)} job runs, "
            f"{len(all_test_results)} test results"
        )
        return all_job_runs, all_test_results

    def collect_all(
        self,
        start_date: datetime,
        end_date: datetime,
        job_patterns: Optional[List[str]] = None,
        versions: Optional[List[str]] = None,
        platforms: Optional[List[str]] = None,
        skip_builds: Optional[set] = None,
        progress_callback=None,
        pr_log_sources: Optional[List[Dict[str, Any]]] = None
    ) -> tuple:
        """Single-pass collection: fetch JUnit XMLs once, return both job runs
        and test results. Skips builds already in the database.

        Args:
            skip_builds: set of (job_name, build_id) tuples to skip
            progress_callback: callable(message) for status updates
            pr_log_sources: optional list of PR log source configs to scan

        Returns:
            (list[JobRun], list[TestResult])
        """
        if not job_patterns:
            raise ValueError("job_patterns is required")

        skip_builds = skip_builds or set()
        resolved_jobs = self._resolve_patterns(job_patterns)
        logger.info(f"[gcsweb] Single-pass collection for {len(resolved_jobs)} job(s), "
                     f"{len(skip_builds)} builds already in DB")

        all_job_runs = []
        all_test_results = []
        max_workers = self.config.get('max_workers', 5)
        jobs_with_no_runs = []
        skipped_count = 0
        fetched_count = 0

        def _process_job(job_name):
            runs = self._list_job_runs(job_name, start_date, end_date, max_results=50)
            if not runs:
                return job_name, [], [], 0, 0

            job_runs = []
            test_results = []
            skipped = 0
            fetched = 0

            for run in runs:
                if (run['job_name'], run['build_id']) in skip_builds:
                    skipped += 1
                    continue

                result = self._process_run_single_pass(run, versions, platforms)
                if result:
                    jr, tr = result
                    job_runs.append(jr)
                    test_results.extend(tr)
                    fetched += 1

            return None, job_runs, test_results, skipped, fetched

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_process_job, jn): jn
                for jn in resolved_jobs
            }

            completed = 0
            for future in as_completed(futures):
                job_name = futures[future]
                completed += 1
                try:
                    empty_job, runs, results, skip_n, fetch_n = future.result()
                    if empty_job:
                        jobs_with_no_runs.append(empty_job)
                    all_job_runs.extend(runs)
                    all_test_results.extend(results)
                    skipped_count += skip_n
                    fetched_count += fetch_n

                    if progress_callback:
                        progress_callback(
                            f'Jobs: {completed}/{len(resolved_jobs)}, '
                            f'{fetched_count} new builds fetched, '
                            f'{skipped_count} skipped (already in DB)'
                        )
                except Exception as e:
                    logger.warning(f"[gcsweb] Error processing job {job_name}: {e}")

        # Scan PR log sources (rehearse, presubmit jobs)
        if pr_log_sources:
            if progress_callback:
                progress_callback('Scanning PR logs...')
            pr_runs, pr_results = self._collect_pr_sources(
                pr_log_sources,
                start_date=start_date,
                end_date=end_date,
                skip_builds=skip_builds,
                versions=versions,
                platforms=platforms,
                progress_callback=progress_callback
            )
            all_job_runs.extend(pr_runs)
            all_test_results.extend(pr_results)
            fetched_count += len(pr_runs)

        logger.info(
            f"[gcsweb] Done: {fetched_count} new builds fetched, "
            f"{skipped_count} skipped, {len(all_job_runs)} job runs, "
            f"{len(all_test_results)} test results"
        )

        if all_job_runs:
            version_counts = {}
            for run in all_job_runs:
                version_counts[run.version] = version_counts.get(run.version, 0) + 1
            for v, count in sorted(version_counts.items()):
                logger.info(f"[gcsweb] Version {v}: {count} new job run(s)")

        if jobs_with_no_runs:
            logger.warning(
                f"[gcsweb] {len(jobs_with_no_runs)} job(s) had no builds "
                f"in window {start_date.strftime('%Y-%m-%d')} to "
                f"{end_date.strftime('%Y-%m-%d')}"
            )

        return all_job_runs, all_test_results

    def _build_job_url(self, run_path: str) -> str:
        """Build the Deck job URL from a run's GCS path.

        Works for both ``logs/`` and ``pr-logs/`` paths by stripping the
        ``/gcs/{bucket}/`` prefix and appending to the Deck base URL.
        """
        prefix = f"/gcs/{self.BUCKET}/"
        if run_path.startswith(prefix):
            relative = run_path[len(prefix):]
        else:
            relative = run_path.lstrip('/')
        return (
            f"https://qe-private-deck-ci.apps.ci.l2s4.p1.openshiftapps.com"
            f"/view/gs/{self.BUCKET}/{relative}"
        )

    def _process_run_single_pass(
        self,
        run: Dict[str, Any],
        versions: Optional[List[str]] = None,
        platforms: Optional[List[str]] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> Optional[tuple]:
        """Process a single run, returning (JobRun, [TestResult]) or None.

        When *start_date*/*end_date* are supplied the run is skipped if
        its ``finished.json`` timestamp falls outside the range.  This is
        used by ``_collect_pr_sources`` where ``_list_pr_jobs`` cannot
        pre-filter by date.
        """
        metadata = self._extract_metadata(run['job_name'])
        if versions and metadata['version'] not in versions:
            return None
        if platforms and metadata['platform'] not in platforms:
            return None

        finished = self._fetch_finished_json(run['path'])
        if not finished:
            return None

        timestamp = finished.get('timestamp')
        if timestamp:
            timestamp = datetime.fromtimestamp(timestamp)
        else:
            timestamp = run.get('timestamp') or datetime.now()

        # Date-range filtering for PR-sourced runs
        if start_date and timestamp < start_date:
            return None
        if end_date and timestamp > end_date:
            return None

        result_status = finished.get('result', 'UNKNOWN')
        status = self._map_status(result_status)
        job_type = self._derive_job_type(run['job_name'])
        job_url = self._build_job_url(run['path'])

        junit_files = self._fetch_junit_xml_files(run['path'])

        total_tests = 0
        passed_tests = 0
        failed_tests = 0
        skipped_tests = 0
        all_results = []

        for junit_root, xml_path in junit_files:
            all_suites = []
            if junit_root.tag == 'testsuite':
                all_suites.append(junit_root)
            all_suites.extend(junit_root.findall('.//testsuite'))

            for testsuite in all_suites:
                if testsuite.findall('testsuite'):
                    continue
                total_tests += int(testsuite.get('tests', 0))
                failed_tests += int(testsuite.get('failures', 0))
                failed_tests += int(testsuite.get('errors', 0))
                skipped_tests += int(testsuite.get('skipped', 0))

            log_url = self._derive_log_url(xml_path)
            results = self._parse_junit_xml(
                junit_root, run['job_name'], run['build_id'], metadata,
                log_url=log_url, job_url=job_url, job_type=job_type
            )
            for r in results:
                r.timestamp = timestamp
            all_results.extend(results)

        passed_tests = total_tests - failed_tests - skipped_tests

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
            job_url=job_url,
            job_type=job_type
        )

        return job_run, all_results

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
        logger.info(f"[gcsweb] Collecting job runs for {len(resolved_jobs)} job(s)")

        job_runs = []
        max_workers = self.config.get('max_workers', 5)
        jobs_with_no_runs = []

        # For each job, list recent runs
        for job_name in resolved_jobs:
            runs = self._list_job_runs(job_name, start_date, end_date, max_results=50)

            if not runs:
                jobs_with_no_runs.append(job_name)

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
                        logger.warning(f"[gcsweb] Error processing run {run['build_id']}: {e}")

        # Log summary by version
        if job_runs:
            version_counts = {}
            for run in job_runs:
                version_counts[run.version] = version_counts.get(run.version, 0) + 1
            for v, count in sorted(version_counts.items()):
                logger.info(f"[gcsweb] Version {v}: {count} job run(s) collected")
        if jobs_with_no_runs:
            logger.warning(
                f"[gcsweb] {len(jobs_with_no_runs)} job(s) returned no builds "
                f"in the lookback window ({start_date.strftime('%Y-%m-%d')} to "
                f"{end_date.strftime('%Y-%m-%d')}): "
                + ", ".join(jobs_with_no_runs)
            )
            # Log per-version breakdown of empty jobs
            empty_by_version = {}
            for job in jobs_with_no_runs:
                meta = self._extract_metadata(job)
                ver = meta['version']
                empty_by_version.setdefault(ver, []).append(job)
            for ver, jobs in sorted(empty_by_version.items()):
                logger.warning(
                    f"[gcsweb] Version {ver}: {len(jobs)} job(s) with no builds"
                )

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
            job_url=self._build_job_url(run['path']),
            job_type=self._derive_job_type(run['job_name'])
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
        logger.info(f"[gcsweb] Collecting test results for {len(resolved_jobs)} job(s)")

        all_results = []
        max_workers = self.config.get('max_workers', 5)
        jobs_with_no_results = []

        for job_name in resolved_jobs:
            runs = self._list_job_runs(job_name, start_date, end_date, max_results=50)

            if not runs:
                jobs_with_no_results.append(job_name)

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
                        logger.warning(f"[gcsweb] Error processing test results for {run['build_id']}: {e}")

        # Log summary
        if all_results:
            version_counts = {}
            for r in all_results:
                version_counts[r.version] = version_counts.get(r.version, 0) + 1
            for v, count in sorted(version_counts.items()):
                logger.info(f"[gcsweb] Version {v}: {count} test result(s) collected")
        if jobs_with_no_results:
            logger.warning(
                f"[gcsweb] {len(jobs_with_no_results)} job(s) returned no builds "
                f"for test result collection: "
                + ", ".join(jobs_with_no_results)
            )

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
