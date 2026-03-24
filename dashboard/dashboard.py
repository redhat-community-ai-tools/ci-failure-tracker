#!/usr/bin/env python3
"""
CI Test Pass Rate Dashboard

Independent tool for tracking test pass rates over time from CI runs.
Supports pluggable data sources (currently: ReportPortal).

Usage:
    ./dashboard.py collect [--days N]      # Collect test results from data source
    ./dashboard.py serve                   # Start web dashboard server
    ./dashboard.py stats                   # Show quick statistics
    ./dashboard.py report --weekly         # Generate weekly platform report

Examples:
    # Collect last 30 days of test results
    ./dashboard.py collect --days 30

    # Start web dashboard
    ./dashboard.py serve --port 8080

    # Show summary statistics
    ./dashboard.py stats

    # Generate weekly report (console format)
    ./dashboard.py report --weekly

    # Generate weekly report (Slack format)
    ./dashboard.py report --weekly --slack

    # Generate report with custom time ranges
    ./dashboard.py report --weekly --current-days 7 --previous-days 7 --top 10
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.table import Table
from rich.progress import Progress

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from collectors.reportportal import ReportPortalCollector
from collectors.prow_gcs import ProwGCSCollector
from collectors.gcsweb import GCSWebCollector
from storage.database import DashboardDatabase
from metrics.calculator import MetricsCalculator
from web.server import create_app
from reports.weekly_report import WeeklyReportGenerator

console = Console()


def load_config(config_path: str = "config.yaml") -> dict:
    """Load configuration from YAML file"""
    config_file = Path(config_path)

    if not config_file.exists():
        console.print(f"[red]Error: Configuration file not found: {config_path}[/red]")
        sys.exit(1)

    with open(config_file, 'r') as f:
        return yaml.safe_load(f)


def get_collector(config: dict):
    """Create data collector based on configuration"""
    collector_type = config['collector']['type']

    if collector_type == 'reportportal':
        rp_config = config['collector']['reportportal']
        return ReportPortalCollector(rp_config)
    elif collector_type == 'prow-gcs':
        prow_config = config['collector']['prow_gcs']
        return ProwGCSCollector(prow_config)
    elif collector_type == 'gcsweb':
        gcsweb_config = config['collector']['gcsweb']
        return GCSWebCollector(gcsweb_config)
    else:
        console.print(f"[red]Error: Unknown collector type: {collector_type}[/red]")
        sys.exit(1)


@click.group()
@click.option('--config', default='config.yaml', help='Path to configuration file')
@click.pass_context
def cli(ctx, config):
    """CI Test Pass Rate Dashboard"""
    ctx.ensure_object(dict)
    ctx.obj['config'] = load_config(config)


@cli.command()
@click.option('--days', default=None, type=int, help='Number of days to look back (overrides config)')
@click.option('--dry-run', is_flag=True, help='Show what would be collected without saving to database')
@click.pass_context
def collect(ctx, days, dry_run):
    """Collect test results from data source"""
    config = ctx.obj['config']

    # Get lookback days
    lookback = days or config['tracking']['lookback_days']

    console.print(f"\n[bold]CI Test Pass Rate Dashboard - Data Collection[/bold]")
    console.print(f"Data Source: {config['collector']['type']}")
    console.print(f"Lookback Period: {lookback} days")

    if dry_run:
        console.print("[yellow]DRY RUN MODE - No data will be saved[/yellow]")

    # Initialize collector
    collector = get_collector(config)

    # Health check
    console.print("\n[bold]Checking data source connection...[/bold]")
    if not collector.health_check():
        console.print("[red]✗ Failed to connect to data source[/red]")
        sys.exit(1)
    console.print("[green]✓ Data source is accessible[/green]")

    # Calculate date range
    end_date = datetime.now()
    start_date = end_date - timedelta(days=lookback)

    console.print(f"\n[bold]Collecting data from {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}...[/bold]")

    # Collect job runs
    collector_type = config['collector']['type']
    versions = config['tracking']['versions']
    platforms = config['tracking']['platforms']

    # Get job patterns based on collector type
    if collector_type == 'reportportal':
        job_patterns = config['collector']['reportportal']['job_patterns']
        # Expand job patterns with versions
        expanded_patterns = []
        for pattern in job_patterns:
            for version in versions:
                expanded_patterns.append(pattern.replace('{version}', version))
    elif collector_type in ['prow-gcs', 'gcsweb']:
        # Prow GCS and gcsweb use exact job names
        key = 'prow_gcs' if collector_type == 'prow-gcs' else 'gcsweb'
        expanded_patterns = config['collector'][key]['job_names']
    else:
        console.print(f"[red]Error: Unknown collector type: {collector_type}[/red]")
        sys.exit(1)

    with Progress() as progress:
        task = progress.add_task("[cyan]Collecting job runs...", total=None)

        job_runs = collector.collect_job_runs(
            start_date=start_date,
            end_date=end_date,
            job_patterns=expanded_patterns,
            versions=versions,
            platforms=platforms
        )

        progress.update(task, completed=True)

    console.print(f"[green]✓ Collected {len(job_runs)} job runs[/green]")

    # Also collect individual test results
    with Progress() as progress:
        task = progress.add_task("[cyan]Collecting individual test results...", total=None)

        test_results = collector.collect_test_results(
            start_date=start_date,
            end_date=end_date,
            job_patterns=expanded_patterns,
            versions=versions,
            platforms=platforms
        )

        progress.update(task, completed=True)

    console.print(f"[green]✓ Collected {len(test_results)} test results[/green]")

    if dry_run:
        # Show sample data
        if job_runs:
            table = Table(title="Sample Job Runs")
            table.add_column("Job Name", style="cyan")
            table.add_column("Version", style="green")
            table.add_column("Platform", style="yellow")
            table.add_column("Pass Rate", style="blue")

            for run in job_runs[:10]:
                table.add_row(
                    run.job_name[:60] + "...",
                    run.version,
                    run.platform,
                    f"{run.pass_rate:.1f}%"
                )

            console.print(table)
    else:
        # Save to database
        db_path = config['database']['path']
        db = DashboardDatabase(db_path)

        console.print(f"\n[bold]Saving data to database: {db_path}[/bold]")

        inserted = db.insert_job_runs(job_runs)
        console.print(f"[green]✓ Saved {inserted} job runs[/green]")

        inserted_tests = db.insert_test_results(test_results)
        console.print(f"[green]✓ Saved {inserted_tests} test results[/green]")

        db.close()

    console.print("\n[green]✓ Data collection completed successfully![/green]")


@cli.command()
@click.option('--host', default=None, help='Host to bind to (default: from config)')
@click.option('--port', default=None, type=int, help='Port to bind to (default: from config)')
@click.option('--debug', is_flag=True, help='Enable debug mode')
@click.pass_context
def serve(ctx, host, port, debug):
    """Start web dashboard server"""
    config = ctx.obj['config']

    db_path = config['database']['path']

    # Check if database exists
    if not Path(db_path).exists():
        console.print(f"[yellow]Warning: Database not found at {db_path}[/yellow]")
        console.print("[yellow]Run 'dashboard.py collect' first to populate the database[/yellow]")

    # Create Flask app
    app = create_app(db_path)

    # Get host and port
    host = host or config['web']['host']
    port = port or config['web']['port']
    debug = debug or config['web']['debug']

    console.print(f"\n[bold]Starting CI Test Pass Rate Dashboard[/bold]")
    console.print(f"Database: {db_path}")
    console.print(f"URL: http://{host}:{port}")
    console.print("\n[yellow]Press Ctrl+C to stop[/yellow]\n")

    app.run(host=host, port=port, debug=debug)


@cli.command()
@click.option('--days', default=7, type=int, help='Number of days to analyze')
@click.pass_context
def stats(ctx, days):
    """Show quick statistics from database"""
    config = ctx.obj['config']
    db_path = config['database']['path']

    if not Path(db_path).exists():
        console.print(f"[red]Error: Database not found at {db_path}[/red]")
        console.print("[yellow]Run 'dashboard.py collect' first[/yellow]")
        sys.exit(1)

    db = DashboardDatabase(db_path)

    # Get blocklist from config
    blocklist = config.get('tracking', {}).get('blocklist', [])
    calculator = MetricsCalculator(db, blocklist=blocklist)

    console.print(f"\n[bold]CI Test Pass Rate Dashboard - Statistics (Last {days} Days)[/bold]\n")

    # Get summary
    summary = calculator.get_summary_stats(days=days)

    console.print(f"[cyan]Date Range:[/cyan] {summary['date_range']}")
    console.print(f"[cyan]Total Runs:[/cyan] {summary['total_runs']}")
    console.print(f"[cyan]Average Pass Rate:[/cyan] {summary['avg_pass_rate']}%")
    console.print(f"[cyan]Trend:[/cyan] {summary['trend']}")

    # Get version comparison
    version_comp = calculator.get_version_comparison(days=days)

    if version_comp['versions']:
        console.print(f"\n[bold]Pass Rates by Version:[/bold]")
        table = Table()
        table.add_column("Version", style="cyan")
        table.add_column("Pass Rate", style="green")
        table.add_column("Total Runs", style="yellow")

        for i, version in enumerate(version_comp['versions']):
            table.add_row(
                version,
                f"{version_comp['pass_rates'][i]:.1f}%",
                str(version_comp['total_runs'][i])
            )

        console.print(table)

    # Get worst performing tests
    test_rankings = calculator.get_test_rankings(days=days, limit=10)

    if test_rankings:
        console.print(f"\n[bold]Top 10 Lowest Performing Tests:[/bold]")
        table = Table()
        table.add_column("Rank", style="cyan")
        table.add_column("Test Name", style="yellow")
        table.add_column("Version", style="blue")
        table.add_column("Pass Rate", style="red")
        table.add_column("Runs", style="green")

        for i, test in enumerate(test_rankings[:10], 1):
            table.add_row(
                str(i),
                test['test_name'],
                test['version'],
                f"{test['pass_rate']:.1f}%",
                str(test['total_runs'])
            )

        console.print(table)

    db.close()


@cli.command()
@click.option('--weekly', is_flag=True, help='Generate weekly report')
@click.option('--current-days', default=7, type=int, help='Days in current period (default: 7)')
@click.option('--previous-days', default=7, type=int, help='Days in previous period (default: 7)')
@click.option('--top', default=5, type=int, help='Number of top failing tests to show (default: 5)')
@click.option('--slack', is_flag=True, help='Output in Slack format')
@click.option('--output', type=click.Path(), help='Save report to file')
@click.pass_context
def report(ctx, weekly, current_days, previous_days, top, slack, output):
    """Generate platform breakdown report"""
    config = ctx.obj['config']
    db_path = config['database']['path']

    if not Path(db_path).exists():
        console.print(f"[red]Error: Database not found at {db_path}[/red]")
        console.print("[yellow]Run 'dashboard.py collect' first[/yellow]")
        sys.exit(1)

    db = DashboardDatabase(db_path)

    # Get blocklist from config
    blocklist = config.get('tracking', {}).get('blocklist', [])
    generator = WeeklyReportGenerator(db, blocklist=blocklist)

    # Generate report
    if weekly:
        if slack:
            report_text = generator.generate_slack_report(
                current_week_days=current_days,
                previous_week_days=previous_days,
                top_failures=top
            )
        else:
            report_text = generator.generate_console_report(
                current_week_days=current_days,
                previous_week_days=previous_days,
                top_failures=top
            )

        # Output report
        if output:
            with open(output, 'w') as f:
                f.write(report_text)
            console.print(f"[green]Report saved to {output}[/green]")
        else:
            print(report_text)
    else:
        console.print("[yellow]Please specify report type: --weekly[/yellow]")
        console.print("[yellow]Example: ./dashboard.py report --weekly --slack[/yellow]")

    db.close()


if __name__ == '__main__':
    cli(obj={})
