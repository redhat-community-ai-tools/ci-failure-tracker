"""
Prow MCP Collector - Uses prow-mcp-server to fetch test results

This collector integrates with the prow-mcp-server to fetch:
- Latest job runs
- Test failures from build artifacts
- Job logs (stdout/stderr)

Repository: https://github.com/redhat-community-ai-tools/prow-mcp-server
"""

import os
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from .base import BaseCollector, TestResult, JobRun, TestStatus


class ProwMCPCollector(BaseCollector):
    """Collector that uses prow-mcp-server for data fetching"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        self.mcp_server_url = config.get('server_url', 'http://localhost:3000')
        self.job_names = config.get('job_names', [])
        self.max_workers = config.get('max_workers', 5)

        # HTTP session for MCP server requests
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })

    @property
    def name(self) -> str:
        return "prow-mcp"

    def health_check(self) -> bool:
        """Check if MCP server is accessible"""
        try:
            # MCP server with SSE transport exposes /sse endpoint
            # Just check if we can connect to the base URL
            response = self.session.get(f"{self.mcp_server_url}/sse", timeout=5, stream=True)
            # SSE endpoint returns 200 even without completing the stream
            return response.status_code == 200
        except Exception as e:
            print(f"Health check failed: {e}")
            return False

    def _call_mcp_tool(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call an MCP tool via HTTP

        MCP servers using SSE transport typically expose tools at:
        POST /mcp/tools/{tool_name}
        """
        try:
            url = f"{self.mcp_server_url}/mcp/tools/{tool_name}"
            response = self.session.post(url, json=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error calling MCP tool {tool_name}: {e}")
            return {}

    def _extract_version_platform(self, job_name: str) -> tuple[str, str]:
        """Extract version and platform from job name"""
        import re

        version = 'unknown'
        platform = 'unknown'

        # Extract version (e.g., 4.21, 4.22)
        version_match = re.search(r'release-(\d+\.\d+)', job_name)
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
        Collect job runs using prow-mcp-server

        Uses get_latest_job_run MCP tool
        """
        job_runs = []

        job_list = job_patterns if job_patterns else self.job_names

        for job_name in job_list:
            try:
                # Call MCP tool: get_latest_job_run
                result = self._call_mcp_tool('get_latest_job_run', {
                    'job_name': job_name
                })

                if not result or 'error' in result:
                    continue

                version, platform = self._extract_version_platform(job_name)

                # Filter by version/platform if specified
                if versions and version not in versions:
                    continue
                if platforms and platform not in platforms:
                    continue

                # Parse job run data from MCP response
                job_run = self._parse_job_run(result, job_name, version, platform)
                if job_run:
                    job_runs.append(job_run)

            except Exception as e:
                print(f"Error collecting job run for {job_name}: {e}")

        return job_runs

    def _parse_job_run(
        self,
        mcp_response: Dict[str, Any],
        job_name: str,
        version: str,
        platform: str
    ) -> Optional[JobRun]:
        """Parse MCP server response into JobRun object"""
        try:
            # Extract data from MCP response
            # Format depends on prow-mcp-server response structure
            build_id = mcp_response.get('build_id', mcp_response.get('id', 'unknown'))
            status_str = mcp_response.get('status', 'UNKNOWN')

            # Map status
            status = TestStatus.PASSED if status_str == 'SUCCESS' else TestStatus.FAILED

            # Timestamps
            start_time = mcp_response.get('start_time')
            if start_time:
                timestamp = datetime.fromtimestamp(start_time)
            else:
                timestamp = datetime.now()

            # Stats (if available in response)
            stats = mcp_response.get('test_stats', {})
            total_tests = stats.get('total', 0)
            passed_tests = stats.get('passed', 0)
            failed_tests = stats.get('failed', 0)
            skipped_tests = stats.get('skipped', 0)

            # Job URL
            job_url = mcp_response.get('url', f"https://prow.ci.openshift.org/view/gs/origin-ci-test/logs/{job_name}/{build_id}")

            return JobRun(
                job_name=job_name,
                build_id=str(build_id),
                status=status,
                timestamp=timestamp,
                duration_seconds=mcp_response.get('duration', 0),
                version=version,
                platform=platform,
                total_tests=total_tests,
                passed_tests=passed_tests,
                failed_tests=failed_tests,
                skipped_tests=skipped_tests,
                job_url=job_url
            )
        except Exception as e:
            print(f"Error parsing job run: {e}")
            return None

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
        Collect test results using prow-mcp-server

        Uses get_test_failures_from_artifacts and get_job_logs MCP tools
        """
        all_results = []

        job_list = job_patterns if job_patterns else self.job_names

        # Collect test results for each job in parallel
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(
                    self._fetch_test_results_for_job,
                    job_name, versions, platforms, test_names
                ): job_name
                for job_name in job_list
            }

            for future in as_completed(futures):
                job_name = futures[future]
                try:
                    results = future.result()
                    all_results.extend(results)
                except Exception as e:
                    print(f"Error fetching tests for {job_name}: {e}")

        return all_results

    def _fetch_test_results_for_job(
        self,
        job_name: str,
        versions: Optional[List[str]],
        platforms: Optional[List[str]],
        test_names: Optional[List[str]]
    ) -> List[TestResult]:
        """Fetch test results for a single job"""
        results = []

        try:
            version, platform = self._extract_version_platform(job_name)

            # Filter by version/platform
            if versions and version not in versions:
                return []
            if platforms and platform not in platforms:
                return []

            # Get latest job run first
            job_info = self._call_mcp_tool('get_latest_job_run', {
                'job_name': job_name
            })

            if not job_info or 'error' in job_info:
                return []

            build_id = job_info.get('build_id', job_info.get('id'))

            # Get test failures from artifacts
            failures = self._call_mcp_tool('get_test_failures_from_artifacts', {
                'job_name': job_name,
                'build_id': str(build_id)
            })

            if not failures:
                return []

            # Parse test failures
            test_list = failures.get('tests', [])

            for test in test_list:
                test_name = test.get('name', 'unknown')

                # Filter by test name if specified
                if test_names and test_name not in test_names:
                    continue

                # Only include OCP-* tests (match dashboard behavior)
                if not test_name.startswith('OCP-'):
                    continue

                # Get logs for failed tests
                error_message = None
                if test.get('status') == 'FAILED':
                    logs = self._call_mcp_tool('get_job_logs', {
                        'job_name': job_name,
                        'build_id': str(build_id)
                    })
                    error_message = logs.get('output', test.get('message', ''))

                result = TestResult(
                    test_name=test_name,
                    status=self._map_test_status(test.get('status', 'UNKNOWN')),
                    timestamp=datetime.fromtimestamp(test.get('timestamp', datetime.now().timestamp())),
                    duration_seconds=test.get('duration', 0),
                    error_message=error_message,
                    job_name=job_name,
                    build_id=str(build_id),
                    version=version,
                    platform=platform,
                    test_description=test.get('description', ''),
                    job_url=job_info.get('url', ''),
                    log_url=f"https://prow.ci.openshift.org/view/gs/origin-ci-test/logs/{job_name}/{build_id}"
                )
                results.append(result)

        except Exception as e:
            print(f"Error fetching test results for {job_name}: {e}")

        return results

    def _map_test_status(self, status_str: str) -> TestStatus:
        """Map test status string to TestStatus enum"""
        status_map = {
            'PASSED': TestStatus.PASSED,
            'SUCCESS': TestStatus.PASSED,
            'FAILED': TestStatus.FAILED,
            'FAILURE': TestStatus.FAILED,
            'SKIPPED': TestStatus.SKIPPED,
            'ERROR': TestStatus.ERROR,
        }
        return status_map.get(status_str.upper(), TestStatus.UNKNOWN)
