"""
ReportPortal data collector

Collects test results from ReportPortal API to calculate pass rates.
"""

import re
import os
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import urllib3

from .base import BaseCollector, TestResult, JobRun, TestStatus

# Disable SSL warnings for internal services
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


class ReportPortalCollector(BaseCollector):
    """Collector for ReportPortal data source"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        self.url = config.get('url', '').rstrip('/')
        self.project = config.get('project', 'prow')
        self.api_token = config.get('api_token') or os.getenv('REPORTPORTAL_API_TOKEN')

        if not self.api_token:
            raise ValueError("ReportPortal API token not provided")

        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {self.api_token}',
            'Content-Type': 'application/json'
        })
        self.session.verify = False  # For internal Red Hat services

    @property
    def name(self) -> str:
        return "reportportal"

    def health_check(self) -> bool:
        """Check if ReportPortal API is accessible"""
        try:
            # Check the launch endpoint with minimal query
            url = f"{self.url}/api/v1/{self.project}/launch"
            params = {'page.size': 1}
            response = self.session.get(url, params=params, timeout=10)
            return response.status_code == 200
        except Exception:
            return False

    def _map_status(self, rp_status: str) -> TestStatus:
        """Map ReportPortal status to normalized TestStatus"""
        status_map = {
            'PASSED': TestStatus.PASSED,
            'FAILED': TestStatus.FAILED,
            'SKIPPED': TestStatus.SKIPPED,
            'INTERRUPTED': TestStatus.ERROR,
        }
        return status_map.get(rp_status, TestStatus.UNKNOWN)

    def _extract_metadata(self, launch_name: str) -> Dict[str, str]:
        """
        Extract version and platform from launch name

        Example: periodic-ci-openshift-openshift-tests-private-release-4.21-amd64-aws-winc-e2e
        Extracts: version="4.21", platform="aws"
        """
        metadata = {'version': 'unknown', 'platform': 'unknown'}

        # Extract version (e.g., 4.21, 4.22)
        version_match = re.search(r'release-(\d+\.\d+)', launch_name)
        if version_match:
            metadata['version'] = version_match.group(1)

        # Extract platform (aws, gcp, azure, vsphere, nutanix, etc.)
        platforms = ['aws', 'gcp', 'azure', 'vsphere', 'nutanix', 'metal', 'ovirt', 'openstack']
        for platform in platforms:
            if platform in launch_name.lower():
                metadata['platform'] = platform
                break

        return metadata

    def collect_job_runs(
        self,
        start_date: datetime,
        end_date: datetime,
        job_patterns: Optional[List[str]] = None,
        versions: Optional[List[str]] = None,
        platforms: Optional[List[str]] = None
    ) -> List[JobRun]:
        """Collect job runs from ReportPortal launches"""

        launches = self._fetch_launches(start_date, end_date, job_patterns)
        job_runs = []

        for launch in launches:
            metadata = self._extract_metadata(launch['name'])

            # Filter by version/platform if specified
            if versions and metadata['version'] not in versions:
                continue
            if platforms and metadata['platform'] not in platforms:
                continue

            # Get statistics
            stats = launch.get('statistics', {}).get('executions', {})
            total = stats.get('total', 0)
            passed = stats.get('passed', 0)
            failed = stats.get('failed', 0)
            skipped = stats.get('skipped', 0)

            job_run = JobRun(
                job_name=launch['name'],
                build_id=str(launch['id']),
                status=self._map_status(launch.get('status', 'UNKNOWN')),
                timestamp=datetime.fromtimestamp(launch['startTime'] / 1000),
                duration_seconds=(launch.get('endTime', launch['startTime']) - launch['startTime']) / 1000,
                version=metadata['version'],
                platform=metadata['platform'],
                total_tests=total,
                passed_tests=passed,
                failed_tests=failed,
                skipped_tests=skipped,
                job_url=f"{self.url}/ui/#{self.project}/launches/all/{launch['id']}"
            )
            job_runs.append(job_run)

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
        """Collect individual test results from ReportPortal"""

        launches = self._fetch_launches(start_date, end_date, job_patterns)
        all_results = []

        # Fetch test items for each launch in parallel
        max_workers = self.config.get('max_workers', 5)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._fetch_test_items, launch, test_names, versions, platforms): launch
                for launch in launches
            }

            for future in as_completed(futures):
                try:
                    results = future.result()
                    all_results.extend(results)
                except Exception as e:
                    launch = futures[future]
                    logger.error(f"[reportportal] Error fetching tests for launch {launch['id']}: {e}")

        return all_results

    def _fetch_launches(
        self,
        start_date: datetime,
        end_date: datetime,
        job_patterns: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Fetch launches from ReportPortal API"""

        url = f"{self.url}/api/v1/{self.project}/launch"

        # Build filter query
        start_ts = int(start_date.timestamp() * 1000)
        end_ts = int(end_date.timestamp() * 1000)

        filter_params = {
            'filter.gte.startTime': start_ts,
            'filter.lte.startTime': end_ts,
            'page.size': self.config.get('page_size', 150),
            'page.sort': 'startTime,DESC'
        }

        # Query for each pattern separately
        launches = []
        max_pages = self.config.get('max_pages', 10)

        if not job_patterns:
            job_patterns = ['winc']  # Default to all WINC jobs

        # ReportPortal filter.cnt.name means "contains" - no wildcards needed
        # Extract the key search term from patterns (remove wildcards)
        patterns = []
        for p in job_patterns:
            # Remove wildcards and {version} placeholders
            # Extract meaningful search terms
            clean = p.replace('*', '').replace('{version}', '')
            # Extract key identifier (e.g., "winc" from the pattern)
            if 'winc' in clean.lower():
                patterns.append('winc')
            elif 'windows' in clean.lower():
                patterns.append('windows')

        # Remove duplicates
        patterns = list(set(patterns)) if patterns else ['winc']

        # Query each pattern separately
        for pattern in patterns:
            pattern_filter = filter_params.copy()
            pattern_filter['filter.cnt.name'] = pattern

            page = 1
            while page <= max_pages:
                pattern_filter['page.page'] = page

                try:
                    response = self.session.get(url, params=pattern_filter, timeout=30)
                    response.raise_for_status()
                    data = response.json()

                    content = data.get('content', [])
                    if not content:
                        break

                    launches.extend(content)

                    # Check if there are more pages
                    if data.get('page', {}).get('totalPages', 0) <= page:
                        break

                    page += 1

                except Exception as e:
                    logger.error(f"[reportportal] Error fetching launches page {page} for pattern '{pattern}': {e}")
                    break

        return launches

    def _fetch_logs_for_item(self, item_id: str) -> Optional[str]:
        """Fetch log messages for a test item"""
        try:
            url = f"{self.url}/api/v1/{self.project}/log"
            params = {
                'filter.eq.item': item_id,
                'page.size': 100
            }

            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            logs = data.get('content', [])
            if not logs:
                return None

            # Combine all log messages
            log_lines = []
            for log in logs:
                message = log.get('message', '').strip()
                if message:
                    log_lines.append(message)

            return '\n'.join(log_lines) if log_lines else None

        except Exception as e:
            logger.error(f"[reportportal] Error fetching logs for item {item_id}: {e}")
            return None

    def _fetch_test_items(
        self,
        launch: Dict[str, Any],
        test_names: Optional[List[str]] = None,
        versions: Optional[List[str]] = None,
        platforms: Optional[List[str]] = None
    ) -> List[TestResult]:
        """Fetch test items for a specific launch"""

        metadata = self._extract_metadata(launch['name'])

        # Filter by version/platform
        if versions and metadata['version'] not in versions:
            return []
        if platforms and metadata['platform'] not in platforms:
            return []

        url = f"{self.url}/api/v1/{self.project}/item"
        params = {
            'filter.eq.launchId': launch['id'],
            'filter.in.type': 'step',  # Only get test steps
            'page.size': 300
        }

        results = []
        page = 1

        while page <= 20:  # Limit pages per launch
            params['page.page'] = page

            try:
                response = self.session.get(url, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()

                content = data.get('content', [])
                if not content:
                    break

                for item in content:
                    test_name, test_description = self._extract_test_name(item['name'])

                    # Filter by test name if specified
                    if test_names and test_name not in test_names:
                        continue

                    # Fetch logs for failed tests only
                    error_message = None
                    item_status = self._map_status(item.get('status', 'UNKNOWN'))
                    if item_status == TestStatus.FAILED:
                        error_message = self._fetch_logs_for_item(str(item['id']))

                    result = TestResult(
                        test_name=test_name,
                        status=item_status,
                        timestamp=datetime.fromtimestamp(item['startTime'] / 1000),
                        duration_seconds=(item.get('endTime', item['startTime']) - item['startTime']) / 1000,
                        error_message=error_message,
                        job_name=launch['name'],
                        build_id=str(launch['id']),
                        version=metadata['version'],
                        platform=metadata['platform'],
                        test_description=test_description,
                        job_url=f"{self.url}/ui/#{self.project}/launches/all/{launch['id']}",
                        log_url=f"{self.url}/ui/#{self.project}/launches/all/{launch['id']}/{item['id']}"
                    )
                    results.append(result)

                if data.get('page', {}).get('totalPages', 0) <= page:
                    break

                page += 1

            except Exception as e:
                logger.error(f"[reportportal] Error fetching test items for launch {launch['id']}: {e}")
                break

        return results

    def _extract_test_name(self, raw_name: str) -> tuple[str, str]:
        """
        Extract clean test name and description from raw name

        Example formats:
        - "OCP-25593:sgao:Windows_Containers:[sig-windows] Windows_Containers Prevent scheduling..."
        - "Smokerun-Author:rrasouli-Medium-37362-[wmco] wmco using correct golang version"

        Returns: ("OCP-XXXXX", "clean description")
        """
        # Try to find OCP-XXXXX pattern
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

            # Remove common prefixes
            description = re.sub(r'^Windows_Containers\s+', '', description)
            description = re.sub(r'^Smokerun-[^\s]+\s+', '', description)

            # Remove [wmco] or similar prefixes at the start
            description = re.sub(r'^\[[\w-]+\]\s+', '', description)

            # Remove all bracketed tags like [Slow], [Disruptive], [Serial]
            description = re.sub(r'\s*\[[\w-]+\]', '', description)

            return (test_id, description.strip() if description else test_id)

        # No OCP ID found - return raw name as both
        return (raw_name.strip(), raw_name.strip())
