"""
Direct GCS Collector - Access Prow test data directly from GCS

Based on prow-mcp-server approach:
1. Fetch job metadata from Prow API (prowjobs.js)
2. Fetch test artifacts from GCS via gcsweb
3. Parse JUnit XML for test results
4. Fetch build logs for failed tests

No MCP, no ReportPortal - direct access to Prow data.
"""

import os
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from .base import BaseCollector, TestResult, JobRun, TestStatus


class ProwGCSCollector(BaseCollector):
    """Collector that accesses Prow data directly via GCS"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        # QE Private Prow (WINC jobs are here)
        self.prow_url = config.get('prow_url', 'https://qe-private-deck-ci.apps.ci.l2s4.p1.openshiftapps.com')
        self.gcs_url = config.get('gcs_url', 'https://gcsweb-qe-private-deck-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/qe-private-deck')

        # Authentication
        self.api_token = self._get_api_token(config)

        # Job patterns
        self.job_names = config.get('job_names', [])
        self.max_workers = config.get('max_workers', 5)

        # HTTP session
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {self.api_token}',
            'Accept': 'application/json'
        })

    def _get_api_token(self, config: Dict[str, Any]) -> str:
        """Get API token from config, environment, or oc CLI"""
        # Try config first
        token = config.get('api_token')
        if token:
            return token

        # Try environment variable
        token = os.environ.get('API_KEY')
        if token:
            return token

        # Try oc CLI
        try:
            token = subprocess.check_output(['oc', 'whoami', '-t'], stderr=subprocess.DEVNULL).decode().strip()
            if token:
                return token
        except Exception:
            pass

        raise ValueError("No API token found. Set API_KEY environment variable or login with 'oc login'")

    @property
    def name(self) -> str:
        return "prow-gcs"

    def health_check(self) -> bool:
        """Check if Prow API is accessible"""
        try:
            url = f"{self.prow_url}/prowjobs.js?var=allBuilds"
            response = self.session.get(url, timeout=5)
            return response.status_code == 200
        except Exception as e:
            print(f"Health check failed: {e}")
            return False

    def _extract_version_platform(self, job_name: str) -> tuple[str, str]:
        """Extract version and platform from job name"""
        version = 'unknown'
        platform = 'unknown'

        # Extract version (e.g., 4.21, 4.22)
        version_match = re.search(r'release-(4\.\d+)', job_name)
        if version_match:
            version = version_match.group(1)

        # Extract platform
        platforms = ['aws', 'gcp', 'azure', 'vsphere', 'nutanix', 'metal']
        for p in platforms:
            if p in job_name.lower():
                platform = p
                break

        return version, platform

    def collect_job_runs(
        self,
        start_date: datetime,
        end_date: datetime,
        job_patterns: Optional[List[str]] = None,
        versions: Optional[List[str]] = None,
        platforms: Optional[List[str]] = None
    ) -> List[JobRun]:
        """
        Collect job runs from Prow API

        Fetches prowjobs.js and filters by job patterns, versions, platforms
        """
        job_runs = []

        try:
            # Fetch all Prow jobs
            url = f"{self.prow_url}/prowjobs.js?var=allBuilds"
            response = self.session.get(url, timeout=30)
            response.raise_for_status()

            # Parse JavaScript response: var allBuilds = {...};
            import json
            content = response.text

            # Remove "var allBuilds = " from start (16 chars) and ";" from end
            json_str = content[16:].rstrip('; \n\r\t')

            data = json.loads(json_str)

            all_jobs = data.get('items', [])
            print(f"Fetched {len(all_jobs)} total jobs from Prow API")

            # Filter jobs
            job_list = job_patterns if job_patterns else self.job_names

            for job in all_jobs:
                spec = job.get('spec', {})
                status = job.get('status', {})
                metadata = job.get('metadata', {})

                job_name = spec.get('job', '')

                # Match job patterns
                if job_list:
                    matched = False
                    for pattern in job_list:
                        # Simple wildcard matching
                        pattern_re = pattern.replace('*', '.*')
                        if re.match(pattern_re, job_name):
                            matched = True
                            break
                    if not matched:
                        continue

                # Extract metadata
                version, platform = self._extract_version_platform(job_name)

                # Filter by version/platform
                if versions and version not in versions:
                    continue
                if platforms and platform not in platforms:
                    continue

                # Parse timestamps
                start_time_str = status.get('startTime')
                completion_time_str = status.get('completionTime')

                if not start_time_str:
                    continue

                start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))

                # Make start_date and end_date timezone-aware if they aren't
                if start_date.tzinfo is None:
                    start_date = start_date.replace(tzinfo=timezone.utc)
                if end_date.tzinfo is None:
                    end_date = end_date.replace(tzinfo=timezone.utc)

                # Filter by date range
                if start_time < start_date or start_time > end_date:
                    continue

                completion_time = None
                if completion_time_str:
                    completion_time = datetime.fromisoformat(completion_time_str.replace('Z', '+00:00'))

                # Calculate duration
                duration = 0
                if completion_time:
                    duration = int((completion_time - start_time).total_seconds())

                # Map state to status
                state = status.get('state', 'unknown')
                job_status = TestStatus.PASSED if state == 'success' else TestStatus.FAILED

                # Build ID
                build_id = status.get('build_id', metadata.get('name', 'unknown'))

                # Job URL
                job_url = status.get('url', f"{self.prow_url}/view/gs/qe-private-deck/logs/{job_name}/{build_id}")

                job_run = JobRun(
                    job_name=job_name,
                    build_id=str(build_id),
                    status=job_status,
                    timestamp=start_time,
                    duration_seconds=duration,
                    version=version,
                    platform=platform,
                    total_tests=0,  # Will be filled from artifacts
                    passed_tests=0,
                    failed_tests=0,
                    skipped_tests=0,
                    job_url=job_url
                )

                job_runs.append(job_run)

            print(f"Filtered to {len(job_runs)} matching job runs")

        except Exception as e:
            print(f"Error collecting job runs: {e}")
            import traceback
            traceback.print_exc()

        return job_runs

    def collect_test_results(
        self,
        start_date: datetime,
        end_date: datetime,
        job_patterns: Optional[List[str]] = None,
        test_names: Optional[List[str]] = None,
        versions: Optional[List[str]] = None,
        platforms: Optional[List[str]] = None
    ) -> List[TestResult]:
        """
        Collect test results from GCS artifacts

        1. Get job runs
        2. For each job, fetch JUnit XML from GCS
        3. Parse test results
        4. Fetch logs for failed tests
        """
        # First get job runs
        job_runs = self.collect_job_runs(start_date, end_date, job_patterns, versions, platforms)

        all_results = []

        # Collect test results in parallel
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(
                    self._fetch_test_results_for_job,
                    job_run, test_names
                ): job_run
                for job_run in job_runs
            }

            for future in as_completed(futures):
                job_run = futures[future]
                try:
                    results = future.result()
                    all_results.extend(results)
                    print(f"Collected {len(results)} tests from {job_run.job_name}/{job_run.build_id}")
                except Exception as e:
                    print(f"Error fetching tests for {job_run.job_name}: {e}")

        print(f"Total test results collected: {len(all_results)}")
        return all_results

    def _fetch_test_results_for_job(
        self,
        job_run: JobRun,
        test_names: Optional[List[str]]
    ) -> List[TestResult]:
        """Fetch test results for a single job from GCS artifacts"""
        results = []

        try:
            # Find JUnit XML files in artifacts
            artifacts_url = f"{self.gcs_url}/logs/{job_run.job_name}/{job_run.build_id}/artifacts/"

            junit_files = self._find_junit_files(artifacts_url)

            for junit_url in junit_files:
                tests = self._parse_junit_xml(junit_url, job_run, test_names)
                results.extend(tests)

            # JUnit XML already contains the test failure messages, no need to fetch build-log.txt

        except Exception as e:
            print(f"Error fetching test results for {job_run.job_name}/{job_run.build_id}: {e}")

        return results

    def _find_junit_files(self, artifacts_url: str, max_depth: int = 5, current_depth: int = 0) -> List[str]:
        """Find JUnit XML files in artifacts directory (recursive search up to 5 levels)"""
        junit_files = []

        if current_depth >= max_depth:
            return junit_files

        try:
            print(f"Searching for junit files at depth {current_depth}: {artifacts_url}")
            response = self.session.get(artifacts_url, timeout=30)
            if response.status_code != 200:
                print(f"Non-200 response ({response.status_code}) from {artifacts_url}")
                return junit_files

            html = response.text

            # Find XML files in current directory (test results may not have "junit" in name)
            xml_pattern = r'href="([^"]*\.xml)"'
            xml_matches = re.findall(xml_pattern, html, re.IGNORECASE)

            for match in xml_matches:
                match = match.strip()
                # Build full URL for junit file
                if match.startswith('http'):
                    junit_url = match
                elif match.startswith('/'):
                    # Absolute path - reconstruct from base
                    base_host = artifacts_url.split('/gcs/')[0]
                    junit_url = base_host + match
                else:
                    # Relative path
                    if match.startswith('./'):
                        match = match[2:]
                    junit_url = artifacts_url.rstrip('/') + '/' + match

                print(f"Found junit file: {junit_url}")
                junit_files.append(junit_url)

            # Only recurse if we haven't hit max depth
            if current_depth < max_depth:
                # Find subdirectories (links ending with /)
                dir_pattern = r'href="([^"]+/)"'
                dir_matches = re.findall(dir_pattern, html)

                for match in dir_matches:
                    match = match.strip()

                    # Skip parent directory and non-test directories
                    if match in ['../', '..', '../', 'metadata/']:
                        continue

                    # Build subdirectory URL
                    if match.startswith('http'):
                        subdir_url = match
                    elif match.startswith('/'):
                        # Absolute path - check if it's a child directory
                        base_host = artifacts_url.split('/gcs/')[0]
                        full_path = base_host + match
                        # Only recurse if this is a subdirectory (longer path than current)
                        if not full_path.rstrip('/').startswith(artifacts_url.rstrip('/')):
                            continue
                        if len(full_path.rstrip('/')) <= len(artifacts_url.rstrip('/')):
                            continue
                        subdir_url = full_path
                    else:
                        # Relative path
                        if match.startswith('./'):
                            match = match[2:]
                        subdir_url = artifacts_url.rstrip('/') + '/' + match

                    # Recursively search subdirectory
                    sub_files = self._find_junit_files(subdir_url, max_depth, current_depth + 1)
                    junit_files.extend(sub_files)

        except Exception as e:
            print(f"Error finding JUnit files at depth {current_depth} in {artifacts_url}: {e}")

        return junit_files

    def _parse_junit_xml(
        self,
        junit_url: str,
        job_run: JobRun,
        test_names: Optional[List[str]]
    ) -> List[TestResult]:
        """Parse JUnit XML file and extract test results"""
        results = []

        try:
            response = self.session.get(junit_url, timeout=10)
            if response.status_code != 200:
                return results

            root = ET.fromstring(response.content)

            # Parse testsuites or testsuite
            testsuites = root.findall('.//testsuite')
            if not testsuites:
                testsuites = [root] if root.tag == 'testsuite' else []

            for testsuite in testsuites:
                for testcase in testsuite.findall('testcase'):
                    test_name = testcase.get('name', 'unknown')

                    # Filter by test name pattern
                    if test_names and test_name not in test_names:
                        continue

                    # Only include OCP-* tests (filter out infrastructure)
                    if not test_name.startswith('OCP-'):
                        continue

                    # Determine status
                    failure = testcase.find('failure')
                    skipped = testcase.find('skipped')
                    error = testcase.find('error')
                    system_out = testcase.find('system-out')

                    if failure is not None:
                        status = TestStatus.FAILED
                        # Include failure message + text + system-out (stdout)
                        error_msg = failure.get('message', '') + '\n' + (failure.text or '')
                        if system_out is not None and system_out.text:
                            error_msg += '\n\nTest Output:\n' + system_out.text
                    elif error is not None:
                        status = TestStatus.ERROR
                        error_msg = error.get('message', '') + '\n' + (error.text or '')
                        if system_out is not None and system_out.text:
                            error_msg += '\n\nTest Output:\n' + system_out.text
                    elif skipped is not None:
                        status = TestStatus.SKIPPED
                        error_msg = None
                    else:
                        status = TestStatus.PASSED
                        error_msg = None

                    # Duration
                    duration_str = testcase.get('time', '0')
                    try:
                        duration = float(duration_str)
                    except ValueError:
                        duration = 0

                    # Description
                    classname = testcase.get('classname', '')

                    result = TestResult(
                        test_name=test_name,
                        status=status,
                        timestamp=job_run.timestamp,
                        duration_seconds=duration,
                        error_message=error_msg,
                        job_name=job_run.job_name,
                        build_id=job_run.build_id,
                        version=job_run.version,
                        platform=job_run.platform,
                        test_description=classname,
                        job_url=job_run.job_url,
                        log_url=f"{self.gcs_url}/logs/{job_run.job_name}/{job_run.build_id}/build-log.txt"
                    )

                    results.append(result)

        except Exception as e:
            print(f"Error parsing JUnit XML {junit_url}: {e}")

        return results

    def _fetch_test_logs(self, job_run: JobRun) -> str:
        """Fetch build logs for a failed test"""
        try:
            log_url = f"{self.gcs_url}/logs/{job_run.job_name}/{job_run.build_id}/build-log.txt"
            response = self.session.get(log_url, timeout=30)

            if response.status_code == 200:
                # Return last 5000 characters (full log can be huge)
                return response.text[-5000:]

        except Exception as e:
            print(f"Error fetching logs for {job_run.job_name}/{job_run.build_id}: {e}")

        return "Logs not available"
