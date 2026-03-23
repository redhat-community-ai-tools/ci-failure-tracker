"""
Prow GCS Bucket Collector

Queries test results directly from Prow's GCS buckets (gs://origin-ci-test/logs/).
Publicly accessible, no authentication needed for OpenShift CI data.
"""

import re
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

import requests

from .base import BaseCollector, TestResult, JobRun, TestStatus


class ProwGCSCollector(BaseCollector):
    """Collector for Prow GCS bucket data source"""

    # GCS HTTP API endpoint
    GCS_BASE_URL = "https://storage.googleapis.com"
    BUCKET = "origin-ci-test"

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'CI-Dashboard-Collector/1.0'
        })

    @property
    def name(self) -> str:
        return "prow-gcs"

    def health_check(self) -> bool:
        """Check if GCS bucket is accessible"""
        try:
            url = f"{self.GCS_BASE_URL}/{self.BUCKET}/"
            response = self.session.get(url, timeout=10)
            return response.status_code == 200
        except Exception:
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
        """
        Extract version and platform from job name

        Example: periodic-ci-openshift-openshift-tests-private-release-4.21-amd64-aws-winc-e2e
        Extracts: version="4.21", platform="aws"
        """
        metadata = {'version': 'unknown', 'platform': 'unknown'}

        # Extract version (e.g., 4.21, 4.22)
        version_match = re.search(r'release-(\d+\.\d+)', job_name)
        if version_match:
            metadata['version'] = version_match.group(1)

        # Extract platform
        platforms = ['aws', 'gcp', 'azure', 'vsphere', 'nutanix', 'metal', 'ovirt', 'openstack']
        for platform in platforms:
            if platform in job_name.lower():
                metadata['platform'] = platform
                break

        return metadata

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
        # GCS path: gs://origin-ci-test/logs/{job-name}/
        prefix = f"logs/{job_name}/"
        url = f"{self.GCS_BASE_URL}/{self.BUCKET}/"

        params = {
            'prefix': prefix,
            'delimiter': '/',
            'maxResults': 1000  # List all build directories
        }

        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()

            # Parse XML response from GCS
            root = ET.fromstring(response.content)
            ns = {'gcs': 'http://doc.s3.amazonaws.com/2006-03-01'}

            runs = []
            for prefix_elem in root.findall('.//gcs:CommonPrefixes/gcs:Prefix', ns):
                path = prefix_elem.text.strip('/')
                # Extract build ID from path: logs/job-name/BUILD_ID/
                parts = path.split('/')
                if len(parts) >= 3:
                    build_id = parts[2]

                    # Try to parse build timestamp from build ID (usually unix timestamp)
                    # Build IDs are typically like: 1234567890
                    try:
                        if build_id.isdigit():
                            build_timestamp = datetime.fromtimestamp(int(build_id))
                        else:
                            # Some builds might have different formats, skip date filtering
                            build_timestamp = datetime.now()

                        # Filter by date
                        if start_date <= build_timestamp <= end_date:
                            runs.append({
                                'job_name': job_name,
                                'build_id': build_id,
                                'path': path,
                                'timestamp': build_timestamp
                            })
                    except (ValueError, OSError):
                        # If we can't parse timestamp, include it anyway
                        runs.append({
                            'job_name': job_name,
                            'build_id': build_id,
                            'path': path,
                            'timestamp': None
                        })

            # Sort by timestamp (most recent first) and limit
            runs = sorted(runs, key=lambda x: x['timestamp'] or datetime.min, reverse=True)
            return runs[:max_results]

        except Exception as e:
            print(f"Error listing job runs for {job_name}: {e}")
            return []

    def _fetch_finished_json(self, run_path: str) -> Optional[Dict[str, Any]]:
        """Fetch finished.json for a job run"""
        url = f"{self.GCS_BASE_URL}/{self.BUCKET}/{run_path}/finished.json"

        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception:
            return None

    def _fetch_junit_xml(self, run_path: str) -> List[ET.Element]:
        """
        Fetch and parse JUnit XML files for a job run

        Returns list of parsed XML root elements
        """
        # Common JUnit XML locations in Prow artifacts
        junit_paths = [
            f"{run_path}/artifacts/junit.xml",
            f"{run_path}/artifacts/junit_*.xml",
            f"{run_path}/artifacts/e2e-*/junit.xml",
        ]

        junit_files = []

        # Try to list artifacts directory
        artifacts_url = f"{self.GCS_BASE_URL}/{self.BUCKET}/{run_path}/artifacts/"

        try:
            response = self.session.get(artifacts_url, timeout=10)
            if response.status_code == 200:
                # Parse directory listing to find JUnit files
                root = ET.fromstring(response.content)
                ns = {'gcs': 'http://doc.s3.amazonaws.com/2006-03-01'}

                for key_elem in root.findall('.//gcs:Contents/gcs:Key', ns):
                    key = key_elem.text
                    if 'junit' in key.lower() and key.endswith('.xml'):
                        junit_url = f"{self.GCS_BASE_URL}/{self.BUCKET}/{key}"
                        try:
                            junit_response = self.session.get(junit_url, timeout=10)
                            if junit_response.status_code == 200:
                                junit_root = ET.fromstring(junit_response.content)
                                junit_files.append(junit_root)
                        except Exception:
                            continue

        except Exception:
            pass

        return junit_files

    def _parse_junit_xml(self, junit_root: ET.Element, job_name: str, build_id: str, metadata: Dict[str, str]) -> List[TestResult]:
        """Parse JUnit XML and extract test results"""
        results = []

        # JUnit format: <testsuite> contains <testcase> elements
        for testsuite in junit_root.findall('.//testsuite'):
            for testcase in testsuite.findall('testcase'):
                name = testcase.get('name', 'unknown')
                classname = testcase.get('classname', '')
                time = float(testcase.get('time', 0))

                # Determine status
                failure = testcase.find('failure')
                error = testcase.find('error')
                skipped = testcase.find('skipped')

                if skipped is not None:
                    status = TestStatus.SKIPPED
                    error_msg = skipped.get('message')
                elif failure is not None:
                    status = TestStatus.FAILED
                    error_msg = failure.get('message') or failure.text
                elif error is not None:
                    status = TestStatus.ERROR
                    error_msg = error.get('message') or error.text
                else:
                    status = TestStatus.PASSED
                    error_msg = None

                # Extract test name (look for OCP-XXXXX pattern)
                test_name = self._extract_test_name(name)

                result = TestResult(
                    test_name=test_name,
                    status=status,
                    timestamp=datetime.now(),  # Will be updated with actual timestamp
                    duration_seconds=time,
                    error_message=error_msg,
                    job_name=job_name,
                    build_id=build_id,
                    version=metadata['version'],
                    platform=metadata['platform'],
                    job_url=f"https://prow.ci.openshift.org/view/gs/{self.BUCKET}/logs/{job_name}/{build_id}",
                    log_url=None
                )
                results.append(result)

        return results

    def _extract_test_name(self, raw_name: str) -> str:
        """Extract clean test name from raw name"""
        # Try to find OCP-XXXXX pattern
        ocp_match = re.search(r'OCP-\d+', raw_name)
        if ocp_match:
            return ocp_match.group(0)

        # Otherwise return cleaned name
        return raw_name.strip()

    def collect_job_runs(
        self,
        start_date: datetime,
        end_date: datetime,
        job_patterns: Optional[List[str]] = None,
        versions: Optional[List[str]] = None,
        platforms: Optional[List[str]] = None
    ) -> List[JobRun]:
        """Collect job runs from Prow GCS buckets"""

        if not job_patterns:
            raise ValueError("job_patterns is required for Prow GCS collector")

        job_runs = []
        max_workers = self.config.get('max_workers', 5)

        # For each job pattern, list recent runs
        for pattern in job_patterns:
            # Prow job names don't use wildcards in GCS paths
            # We need exact job names, so we'll expand patterns
            job_name = pattern.replace('*', '')  # This is simplified - may need better logic

            runs = self._list_job_runs(job_name, start_date, end_date, max_results=50)

            # Fetch finished.json for each run in parallel
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

        # Fetch JUnit XML to count test results
        junit_files = self._fetch_junit_xml(run['path'])

        total_tests = 0
        passed_tests = 0
        failed_tests = 0
        skipped_tests = 0

        for junit_root in junit_files:
            for testsuite in junit_root.findall('.//testsuite'):
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
            job_url=f"https://prow.ci.openshift.org/view/gs/{self.BUCKET}/logs/{run['job_name']}/{run['build_id']}"
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
        """Collect individual test results from Prow GCS buckets"""

        if not job_patterns:
            raise ValueError("job_patterns is required for Prow GCS collector")

        all_results = []
        max_workers = self.config.get('max_workers', 5)

        for pattern in job_patterns:
            job_name = pattern.replace('*', '')
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
        junit_files = self._fetch_junit_xml(run['path'])

        all_results = []
        for junit_root in junit_files:
            results = self._parse_junit_xml(junit_root, run['job_name'], run['build_id'], metadata)

            # Update timestamps
            for result in results:
                result.timestamp = timestamp

            # Filter by test name
            if test_names:
                results = [r for r in results if r.test_name in test_names]

            all_results.extend(results)

        return all_results
