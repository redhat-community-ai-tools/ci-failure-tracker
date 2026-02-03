#!/usr/bin/env python3
"""
CI Failure Tracker - ReportPortal to Jira Bridge

Analyzes CI test failures from ReportPortal and creates Jira tickets
for failed test cases.

Features:
- Fetches failed test cases from ReportPortal launches
- Creates one Jira ticket per unique test case (e.g., OCP-11111)
- Aggregates failures across multiple platforms (AWS, GCP, Azure, etc.)
- Links to all failure instances in ReportPortal
- Configurable per-team via YAML configuration

Usage:
    ./ci_failure_tracker.py --team TEAM_ID [OPTIONS]

Options:
    --team TEXT            Team ID (required) - must match a file in teams/
    --dry-run             Don't create tickets, just analyze
    --verbose             Enable verbose output
    --days N              Override lookback days from config
    --page-size N         Number of launches per API page (default: 150)
    --max-pages N         Maximum pages to fetch (default: 5)
    --max-workers N       Number of parallel workers (default: from config)
    --help                Show this help message

Examples:
    # Analyze last 7 days of failures
    ./ci_failure_tracker.py --team winc --dry-run --days 7

    # Fetch more launches
    ./ci_failure_tracker.py --team winc --dry-run --days 7 --max-pages 20

    # Use larger page sizes
    ./ci_failure_tracker.py --team winc --dry-run --page-size 300 --max-pages 3
"""

import os
import sys
import hashlib
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import click
import requests
import urllib3
from rich.console import Console
from rich.table import Table
from rich.progress import track, Progress

# Disable SSL warnings for internal Red Hat services
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Import our custom modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
from core.config_loader import ConfigLoader, TeamConfig
from core.jira_client import get_jira_client, JiraTicket

console = Console()


@dataclass
class FailureInstance:
    """Single failure occurrence"""
    launch_id: str
    launch_name: str
    test_name: str
    error_message: str
    timestamp: datetime
    version: str
    platform: str
    job_url: str
    reportportal_url: str


@dataclass
class FailurePattern:
    """Aggregated failure pattern"""
    test_name: str
    error_signature: str
    error_message: str
    instances: List[FailureInstance]
    versions: List[str]
    platforms: List[str]
    first_seen: datetime
    last_seen: datetime

    @property
    def count(self) -> int:
        return len(self.instances)


class ReportPortalClient:
    """Client for ReportPortal API"""

    def __init__(self, api_url: str, project: str, api_token: str, page_size: int = 150, max_pages: int = 5):
        self.api_url = api_url.rstrip('/')
        self.project = project
        self.page_size = page_size
        self.max_pages = max_pages
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json"
        }

    def get_failed_launches(
        self,
        job_pattern: str,
        start_date: datetime,
        end_date: datetime,
        filter_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Get failed launches matching pattern"""
        try:
            url = f"{self.api_url}/api/v1/{self.project}/launch"

            # Fetch multiple pages to ensure we get all matching launches
            all_launches = []
            page = 1

            while page <= self.max_pages:
                params = {
                    "filter.gte.startTime": int(start_date.timestamp() * 1000),
                    "filter.lte.startTime": int(end_date.timestamp() * 1000),
                    "filter.in.status": "FAILED",  # Only get failed launches (use .in instead of .eq)
                    "page.size": self.page_size,
                    "page.page": page,
                    "page.sort": "startTime,DESC"
                }

                # Use saved filter if provided (note: filterId is at URL level, not a param)
                # if filter_id:
                #     url = f"{url}?filterId={filter_id}"

                # Add name filter for better server-side filtering
                # Use .cnt (contains) for partial matching
                if '-winc-' in job_pattern:
                    params["filter.cnt.name"] = "-winc-"
                elif 'windows-machine-config-operator' in job_pattern:
                    params["filter.cnt.name"] = "windows-machine-config-operator"

                response = requests.get(
                    url,
                    headers=self.headers,
                    params=params,
                    timeout=60,  # Increased timeout
                    verify=False
                )

                if response.status_code == 200:
                    data = response.json()
                    launches = data.get('content', [])

                    if not launches:  # No more results
                        break

                    all_launches.extend(launches)

                    # Check if there are more pages
                    page_info = data.get('page', {})
                    total_pages = page_info.get('totalPages', 1)

                    if page >= total_pages:
                        break

                    page += 1
                else:
                    console.print(f"[red]ReportPortal API error: {response.status_code}[/red]")
                    break

            # DEBUG: Show what API returned
            if all_launches:
                oldest_launch_time = min(launch.get('startTime', 0) for launch in all_launches) / 1000
                newest_launch_time = max(launch.get('startTime', 0) for launch in all_launches) / 1000
                oldest_date = datetime.fromtimestamp(oldest_launch_time).strftime('%Y-%m-%d %H:%M')
                newest_date = datetime.fromtimestamp(newest_launch_time).strftime('%Y-%m-%d %H:%M')
                console.print(f"[dim]    API returned {len(all_launches)} launches across {page} page(s) ({oldest_date} to {newest_date})[/dim]")
            else:
                console.print(f"[dim]    API returned 0 launches[/dim]")

            # Filter by job pattern (client-side filtering)
            filtered = []
            sample_names = []
            version_matches = []  # Track launches that match version but not full pattern

            # Extract version from job_pattern for partial matching
            version_from_pattern = None
            if '-release-' in job_pattern:
                version_from_pattern = job_pattern.split('-release-')[1].split('-')[0]

            for launch in all_launches:
                launch_name = launch.get('name', '')
                if self._matches_pattern(launch_name, job_pattern):
                    filtered.append(launch)
                else:
                    # Check if it matches the version but not the full pattern
                    if version_from_pattern and f'-release-{version_from_pattern}-' in launch_name:
                        version_matches.append(launch_name)
                    elif len(sample_names) < 3:
                        sample_names.append(launch_name)

            # DEBUG: Show version matches or sample launch names if nothing matched
            if not filtered:
                if version_matches:
                    console.print(f"[dim]    Found {len(version_matches)} launches for version but pattern didn't match[/dim]")

                    # Look for launches containing "winc" or "windows" in the name
                    winc_related = [name for name in version_matches if 'winc' in name.lower() or 'windows' in name.lower()]
                    if winc_related:
                        console.print(f"[dim]    Found {len(winc_related)} winc/windows launches: {winc_related[0][:100]}...[/dim]")
                    else:
                        console.print(f"[dim]    No 'winc' or 'windows' in any launch names[/dim]")

                    console.print(f"[dim]    Sample: {version_matches[0][:100]}...[/dim]")
                elif sample_names:
                    console.print(f"[dim]    Sample launch names (not matching): {', '.join(sample_names[:2])}...[/dim]")

            return filtered

        except Exception as e:
            console.print(f"[red]Error fetching launches: {e}[/red]")
            return []

    def _matches_pattern(self, name: str, pattern: str) -> bool:
        """Check if launch name matches pattern (simple wildcard matching)"""
        # Convert glob pattern to regex
        # Replace {version} with \d+\.\d+
        # Replace * with .*
        regex_pattern = pattern.replace('{version}', r'\d+\.\d+')
        regex_pattern = regex_pattern.replace('*', '.*')
        regex_pattern = f"^{regex_pattern}$"

        return bool(re.match(regex_pattern, name))

    def get_failed_test_items(self, launch_id: str) -> List[Dict[str, Any]]:
        """Get failed test items from a launch - get ALL nested levels"""
        try:
            url = f"{self.api_url}/api/v1/{self.project}/item"

            # First, get all failed items (including containers)
            params = {
                "filter.eq.launchId": launch_id,
                "filter.in.status": "FAILED,INTERRUPTED",
                "page.size": 500
            }

            response = requests.get(
                url,
                headers=self.headers,
                params=params,
                timeout=30,
                verify=False
            )

            if response.status_code == 200:
                data = response.json()
                all_items = data.get('content', [])

                # Filter to only leaf nodes (actual tests, not suites)
                leaf_items = [
                    item for item in all_items
                    if not item.get('hasChildren', True) and item.get('type') == 'STEP'
                ]

                if leaf_items:
                    console.print(f"[dim]    → Found {len(leaf_items)} failed test cases[/dim]")

                return leaf_items
            else:
                console.print(f"[red]ReportPortal items API error: {response.status_code}[/red]")
                return []

        except Exception as e:
            console.print(f"[red]Error fetching test items: {e}[/red]")
            return []

    def get_test_item_logs(self, item_id: str) -> str:
        """Get error logs for a test item"""
        try:
            url = f"{self.api_url}/api/v1/{self.project}/log"

            params = {
                "filter.eq.item": item_id,
                "filter.gte.level": "40000",  # ERROR level and above
                "page.size": 10
            }

            response = requests.get(
                url,
                headers=self.headers,
                params=params,
                timeout=30,
                verify=False
            )

            if response.status_code == 200:
                data = response.json()
                logs = data.get('content', [])
                if logs:
                    # Concatenate error messages
                    return "\n".join([log.get('message', '') for log in logs])
                return "No error message available"
            else:
                return "Failed to fetch logs"

        except Exception as e:
            return f"Error fetching logs: {e}"


def generate_error_signature(error_message: str) -> str:
    """Generate normalized error signature for deduplication"""
    # Remove transient data
    normalized = error_message

    # Remove IPs
    normalized = re.sub(r'\d+\.\d+\.\d+\.\d+', 'IP', normalized)

    # Remove timestamps
    normalized = re.sub(r'\d{4}-\d{2}-\d{2}', 'DATE', normalized)
    normalized = re.sub(r'\d{2}:\d{2}:\d{2}', 'TIME', normalized)

    # Remove pod names
    normalized = re.sub(r'pod-[a-z0-9-]+', 'POD', normalized)
    normalized = re.sub(r'[a-z0-9]+-[a-z0-9]+-[a-z0-9]+', 'RESOURCE', normalized)

    # Remove numbers that might be transient
    normalized = re.sub(r'\d+', 'NUM', normalized)

    # Hash it
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def analyze_failures(
    instances: List[FailureInstance],
    threshold: int
) -> List[FailurePattern]:
    """
    Group failures by test case name only.

    Creates one ticket per unique test case (e.g., OCP-11111) regardless of:
    - Which platform it failed on (AWS, GCP, Azure, etc.)
    - What the error message was
    - How many times it failed

    The resulting ticket will aggregate all failures and list all platforms.
    """

    # Group by test_name ONLY (not error signature)
    patterns_dict = defaultdict(list)

    for instance in instances:
        key = instance.test_name
        patterns_dict[key].append(instance)

    # Convert to FailurePattern objects
    patterns = []
    for test_name, instance_list in patterns_dict.items():
        # Skip if below threshold
        if len(instance_list) < threshold:
            continue

        # Use first error message as representative
        error_signature = generate_error_signature(instance_list[0].error_message)

        pattern = FailurePattern(
            test_name=test_name,
            error_signature=error_signature,
            error_message=instance_list[0].error_message,
            instances=instance_list,
            versions=sorted(set(i.version for i in instance_list)),
            platforms=sorted(set(i.platform for i in instance_list)),
            first_seen=min(i.timestamp for i in instance_list),
            last_seen=max(i.timestamp for i in instance_list)
        )
        patterns.append(pattern)

    return sorted(patterns, key=lambda p: p.count, reverse=True)


def process_launch(
    launch: Dict[str, Any],
    version: str,
    team_config: TeamConfig,
    rp_client: ReportPortalClient,
    verbose: bool = False
) -> List[FailureInstance]:
    """Process a single launch and extract failure instances"""
    instances = []

    launch_id = launch.get('id', '')
    launch_name = launch.get('name', '')
    launch_timestamp = datetime.fromtimestamp(launch.get('startTime', 0) / 1000)

    # Extract platform from launch name
    all_platforms = ["aws", "azure", "gcp", "vsphere", "nutanix", "metal", "ovirt", "ibmcloud", "alibabacloud"]
    platform = "unknown"
    for p in all_platforms:
        if p in launch_name.lower():
            platform = p
            break

    # Skip launches from platforms not in config
    if platform != "unknown" and platform not in team_config.platforms:
        return instances

    # Get failed test items
    test_items = rp_client.get_failed_test_items(launch_id)

    for test_item in test_items:
        test_name_raw = test_item.get('name', 'Unknown')
        test_id = test_item.get('id', '')
        test_description = test_item.get('description', '')
        test_code_ref = test_item.get('codeRef', '')
        test_parameters = test_item.get('parameters', [])
        test_attributes = test_item.get('attributes', [])

        # Extract test identifier from ALL available fields
        test_name = test_name_raw
        search_fields = [
            test_name_raw,
            test_description,
            test_code_ref,
            str(test_parameters),
            str(test_attributes)
        ]

        # 1. Try OCP-XXXXX format
        for field in search_fields:
            ocp_match = re.search(r'(OCP-\d+)', str(field))
            if ocp_match:
                test_name = ocp_match.group(1)
                break

        if test_name == test_name_raw:  # Didn't find OCP-XXXXX
            # 2. Try Critical-XXXXX format
            for field in search_fields:
                critical_match = re.search(r'Critical-(\d+)', str(field))
                if critical_match:
                    test_name = f"Critical-{critical_match.group(1)}"
                    break

        if test_name == test_name_raw:  # Still didn't find it
            # 3. Try standalone 5-digit number
            for field in search_fields:
                number_match = re.search(r'(\d{5})', str(field))
                if number_match:
                    test_name = number_match.group(1)
                    break

        # Skip test items without proper test identifiers
        if test_name == test_name_raw:
            continue

        # Get error logs
        error_message = rp_client.get_test_item_logs(test_id)

        # Build ReportPortal URL
        rp_url = f"{team_config.reportportal_url}/ui/#{team_config.reportportal_project}/launches/all/{launch_id}/log?item={test_id}"

        instance = FailureInstance(
            launch_id=launch_id,
            launch_name=launch_name,
            test_name=test_name,
            error_message=error_message,
            timestamp=launch_timestamp,
            version=version,
            platform=platform,
            job_url=launch.get('metadata', {}).get('url', ''),
            reportportal_url=rp_url
        )
        instances.append(instance)

    return instances


def create_ticket_description(pattern: FailurePattern, team_config: TeamConfig) -> str:
    """
    Generate Jira ticket description in wiki markup.

    Creates a comprehensive ticket showing all failures across all platforms
    with links to each failure instance in ReportPortal.
    """

    versions_str = ", ".join(pattern.versions)
    platforms_str = ", ".join(pattern.platforms)

    # Group failures by platform for better organization
    by_platform = defaultdict(list)
    for instance in pattern.instances:
        by_platform[instance.platform].append(instance)

    description = f"""h2. Test Case Failure Report

*Test Case*: {pattern.test_name}
*Affected Versions*: {versions_str}
*Affected Platforms*: {platforms_str}

h2. Summary

* *Total Failures*: {pattern.count} in last {team_config.lookback_days} days
* *First Seen*: {pattern.first_seen.strftime('%Y-%m-%d %H:%M')}
* *Last Seen*: {pattern.last_seen.strftime('%Y-%m-%d %H:%M')}
"""

    # Add platform breakdown
    for platform in sorted(by_platform.keys()):
        platform_instances = by_platform[platform]
        description += f"* *{platform.upper()}*: {len(platform_instances)} failure(s)\n"

    description += f"""
h2. Sample Error Message

{{code}}
{pattern.error_message[:500]}
{{code}}

h2. Failure Details by Platform
"""

    # List all failures grouped by platform
    for platform in sorted(by_platform.keys()):
        platform_instances = sorted(by_platform[platform], key=lambda x: x.timestamp, reverse=True)
        description += f"\nh3. {platform.upper()} ({len(platform_instances)} failure(s))\n\n"
        description += "||Date||Version||Launch||ReportPortal Link||\n"

        for instance in platform_instances:
            date_str = instance.timestamp.strftime('%Y-%m-%d %H:%M')
            launch_short = instance.launch_name[:50] + "..." if len(instance.launch_name) > 50 else instance.launch_name
            description += f"|{date_str}|{instance.version}|{launch_short}|[View Logs|{instance.reportportal_url}]|\n"

    description += f"""
h2. Investigation Steps

# Review test case definition and recent changes
# Check failure patterns across platforms (are they all the same issue?)
# Investigate error logs in ReportPortal for each platform
# Determine if this is a test infrastructure issue or product bug
# Check if failures correlate with specific OCP versions

---
_This ticket was automatically created by CI Failure Tracker_
_Team: {team_config.team_name}_
_Analysis Period: Last {team_config.lookback_days} days_
"""

    return description


@click.command()
@click.option('--team', required=True, help='Team ID (e.g., "winc")')
@click.option('--dry-run', is_flag=True, help="Don't create tickets, just analyze")
@click.option('--verbose', is_flag=True, help='Enable verbose output')
@click.option('--days', type=int, help='Override lookback days from config')
@click.option('--page-size', type=int, default=150, help='Number of launches per page (default: 150)')
@click.option('--max-pages', type=int, default=5, help='Maximum pages to fetch (default: 5)')
@click.option('--max-workers', type=int, help='Number of parallel workers for processing launches')
def main(team: str, dry_run: bool, verbose: bool, days: Optional[int], page_size: int, max_pages: int, max_workers: Optional[int]):
    """CI Failure Tracker - Multi-team ReportPortal to Jira bridge"""

    console.print(f"\n[bold blue]CI Failure Tracker - Team: {team.upper()}[/bold blue]")
    console.print(f"Run at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    if dry_run:
        console.print("[yellow]Mode: DRY RUN (no tickets will be created)[/yellow]\n")

    # Load team configuration
    try:
        config_loader = ConfigLoader()
        team_config = config_loader.load_team(team)
        console.print(f"[green]✓ Loaded config for team: {team_config.team_name}[/green]")
    except FileNotFoundError:
        console.print(f"[red]Error: Team config not found: teams/{team}.yaml[/red]")
        console.print(f"[dim]Available teams: {', '.join(config_loader.list_teams())}[/dim]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error loading team config: {e}[/red]")
        sys.exit(1)

    # Override days if specified
    lookback_days = days if days is not None else team_config.lookback_days

    # Override max_workers if specified
    if max_workers is not None:
        team_config.max_workers = max_workers

    console.print(f"Tracking versions: {', '.join(team_config.versions)}")
    console.print(f"Looking back: {lookback_days} days")
    console.print(f"Failure threshold: {team_config.failure_threshold}")
    console.print(f"Page size: {page_size}, Max pages: {max_pages}, Workers: {team_config.max_workers}\n")

    # Initialize ReportPortal client
    rp_token = os.environ.get('REPORTPORTAL_API_TOKEN')
    if not rp_token:
        console.print("[red]Error: REPORTPORTAL_API_TOKEN environment variable not set[/red]")
        sys.exit(1)

    rp_client = ReportPortalClient(
        api_url=team_config.reportportal_url,
        project=team_config.reportportal_project,
        api_token=rp_token,
        page_size=page_size,
        max_pages=max_pages
    )

    # Initialize Jira client
    try:
        jira_client = get_jira_client(
            jira_url=team_config.jira_url,
            project=team_config.jira_project
        )
        console.print(f"[green]✓ Jira client initialized[/green]\n")
    except ValueError as e:
        if not dry_run:
            console.print(f"[red]Error: {e}[/red]")
            console.print("[yellow]Tip: For dry-run, only REPORTPORTAL_API_TOKEN is needed[/yellow]")
            sys.exit(1)
        else:
            console.print("[yellow]⚠ Jira credentials not set (OK for dry-run)[/yellow]\n")
            jira_client = None

    # Fetch failures from ReportPortal
    console.print("[bold]Querying ReportPortal...[/bold]")

    end_date = datetime.now()
    start_date = end_date - timedelta(days=lookback_days)

    all_instances = []

    for version in team_config.versions:
        console.print(f"\n[bold cyan]Version {version}[/bold cyan]")
        for pattern in team_config.job_patterns:
            job_pattern = pattern.format(version=version)

            console.print(f"[dim]  Pattern: {job_pattern}[/dim]")

            launches = rp_client.get_failed_launches(
                job_pattern,
                start_date,
                end_date,
                filter_id=team_config.reportportal_filter_id
            )

            console.print(f"[dim]    → {len(launches)} launches matched[/dim]")

            # Process launches sequentially (parallel processing causes Rich console issues)
            launches_with_failures = 0
            for launch in launches:
                instances = process_launch(launch, version, team_config, rp_client, verbose)
                if instances:
                    all_instances.extend(instances)
                    launches_with_failures += 1
                    console.print(f"[dim]      Launch {launch.get('id')}: {len(instances)} test cases[/dim]")

            if launches:
                console.print(f"[dim]    Summary: {launches_with_failures}/{len(launches)} launches had test failures[/dim]")


    console.print(f"\n[green]Total failed test cases found: {len(all_instances)}[/green]\n")

    if not all_instances:
        console.print("[yellow]No failures found in the lookback period[/yellow]")
        return

    # Analyze patterns
    console.print("[bold]Analyzing failure patterns...[/bold]")
    patterns = analyze_failures(all_instances, team_config.failure_threshold)

    console.print(f"[green]Found {len(patterns)} failure patterns above threshold[/green]\n")

    if not patterns:
        console.print("[yellow]No patterns exceed the failure threshold[/yellow]")
        return

    # Display results table
    table = Table(title="Failure Patterns", show_lines=True)
    table.add_column("Test", style="cyan", no_wrap=True)
    table.add_column("Versions", style="yellow", max_width=14)
    table.add_column("Platforms", style="magenta", max_width=14)
    table.add_column("Failures", justify="right", style="green")
    table.add_column("Status", style="blue", max_width=17)

    tickets_created = 0
    tickets_skipped = 0

    for pattern in patterns:
        versions_str = ", ".join(pattern.versions)
        platforms_str = ", ".join(pattern.platforms)

        # Check for duplicates
        if jira_client and not dry_run:
            existing = jira_client.check_for_duplicate(pattern.test_name, pattern.error_signature)
            if existing:
                status = f"Exists:\n{existing}"
                tickets_skipped += 1
            else:
                # Create ticket
                summary = f"CI Failure: {pattern.test_name} - {versions_str}"
                description = create_ticket_description(pattern, team_config)

                ticket = jira_client.create_issue(
                    project=team_config.jira_project,
                    issue_type=team_config.jira_issue_type,
                    summary=summary,
                    description=description,
                    parent=team_config.jira_parent_epic,
                    labels=team_config.jira_labels,
                    dry_run=dry_run
                )

                if ticket:
                    status = f"Created:\n{ticket.key}"
                    tickets_created += 1
                else:
                    status = "Failed to\ncreate"
        else:
            status = "[DRY RUN]\nWould create"

        table.add_row(
            pattern.test_name,
            versions_str,
            platforms_str,
            str(pattern.count),
            status
        )

    console.print(table)

    # Summary
    console.print(f"\n[bold]Summary:[/bold]")
    console.print(f"  Total patterns: {len(patterns)}")
    console.print(f"  Tickets created: {tickets_created}")
    console.print(f"  Tickets skipped (existing): {tickets_skipped}")

    if dry_run:
        console.print(f"\n[yellow]This was a dry run. No tickets were created.[/yellow]")

    console.print()


if __name__ == '__main__':
    main()
