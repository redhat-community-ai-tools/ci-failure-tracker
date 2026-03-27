"""
Flask web server for dashboard
"""

from flask import Flask, render_template, jsonify, request
from datetime import datetime, timedelta
from pathlib import Path
import yaml
import threading
import sys
import os
import logging

from storage.database import DashboardDatabase
from metrics.calculator import MetricsCalculator
from reports.weekly_report import WeeklyReportGenerator

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Global collection status
collection_status = {
    'running': False,
    'progress': '',
    'error': None,
    'completed_at': None,
    'lock': threading.Lock()
}


def run_collection_background(db_path: str, config_file: str = 'config.yaml', days: int = 30):
    """Run data collection in background thread"""
    global collection_status

    try:
        logger.info(f"Starting data collection for {days} days")
        collection_status['progress'] = 'Starting collection...'

        # Load config
        with open(config_file, 'r') as f:
            config = yaml.safe_load(f)

        # Import collector modules
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

        # Initialize collector based on type
        collector_type = config['collector']['type']
        logger.info(f"Using collector type: {collector_type}")

        if collector_type == 'reportportal':
            from collectors.reportportal import ReportPortalCollector
            rp_config = config['collector']['reportportal']
            collector = ReportPortalCollector(rp_config)
        elif collector_type == 'prow_mcp':
            from collectors.prow_mcp import ProwMCPCollector
            mcp_config = config['collector']['prow_mcp']
            collector = ProwMCPCollector(mcp_config)
        elif collector_type == 'prow_gcs':
            from collectors.prow_gcs import ProwGCSCollector
            gcs_config = config['collector']['prow_gcs']
            collector = ProwGCSCollector(gcs_config)
        else:
            error_msg = f'Unsupported collector type: {collector_type}'
            logger.error(error_msg)
            collection_status['error'] = error_msg
            collection_status['running'] = False
            return

        # Health check
        logger.info("Running health check...")
        collection_status['progress'] = 'Checking data source...'
        if not collector.health_check():
            error_msg = 'Failed to connect to data source'
            logger.error(error_msg)
            collection_status['error'] = error_msg
            collection_status['running'] = False
            return

        # Calculate date range
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        # Get job patterns based on collector type
        versions = config['tracking']['versions']
        platforms = config['tracking']['platforms']

        if collector_type == 'reportportal':
            job_patterns = config['collector']['reportportal']['job_patterns']
            # Expand patterns with version placeholders
            expanded_patterns = []
            for pattern in job_patterns:
                for version in versions:
                    expanded_patterns.append(pattern.replace('{version}', version))
        elif collector_type == 'prow_gcs':
            # prow_gcs uses wildcard patterns, no version expansion needed
            # Support both 'job_patterns' (new) and 'job_names' (legacy)
            prow_gcs_config = config['collector']['prow_gcs']
            expanded_patterns = prow_gcs_config.get('job_patterns') or prow_gcs_config.get('job_names', [])
        elif collector_type == 'prow_mcp':
            # prow_mcp uses exact job names from config
            expanded_patterns = None  # Will use job_names from collector config
        else:
            expanded_patterns = []

        # Collect job runs
        logger.info("Collecting job runs...")
        collection_status['progress'] = 'Collecting job runs...'
        job_runs = collector.collect_job_runs(
            start_date=start_date,
            end_date=end_date,
            job_patterns=expanded_patterns,
            versions=versions,
            platforms=platforms
        )
        logger.info(f"Collected {len(job_runs)} job runs")

        # Collect test results
        collection_status['progress'] = f'Collected {len(job_runs)} job runs, collecting test results...'
        logger.info("Collecting test results (fetching logs for failed tests)...")
        test_results = collector.collect_test_results(
            start_date=start_date,
            end_date=end_date,
            job_patterns=expanded_patterns,
            versions=versions,
            platforms=platforms
        )
        logger.info(f"Collected {len(test_results)} test results")

        # Save to database
        collection_status['progress'] = f'Collected {len(test_results)} test results, saving to database...'
        logger.info("Saving to database...")
        db = DashboardDatabase(db_path)

        inserted_jobs = db.insert_job_runs(job_runs)
        inserted_tests = db.insert_test_results(test_results)

        # Update job_runs with actual test counts from test_results
        logger.info("Updating job runs with test counts...")
        db.conn.execute("""
            UPDATE job_runs
            SET
                total_tests = (
                    SELECT COUNT(*) FROM test_results
                    WHERE test_results.job_name = job_runs.job_name
                    AND test_results.build_id = job_runs.build_id
                    AND test_results.status != 'skipped'
                ),
                passed_tests = (
                    SELECT COUNT(*) FROM test_results
                    WHERE test_results.job_name = job_runs.job_name
                    AND test_results.build_id = job_runs.build_id
                    AND test_results.status = 'passed'
                ),
                failed_tests = (
                    SELECT COUNT(*) FROM test_results
                    WHERE test_results.job_name = job_runs.job_name
                    AND test_results.build_id = job_runs.build_id
                    AND test_results.status = 'failed'
                ),
                skipped_tests = (
                    SELECT COUNT(*) FROM test_results
                    WHERE test_results.job_name = job_runs.job_name
                    AND test_results.build_id = job_runs.build_id
                    AND test_results.status = 'skipped'
                )
            WHERE EXISTS (
                SELECT 1 FROM test_results
                WHERE test_results.job_name = job_runs.job_name
                AND test_results.build_id = job_runs.build_id
            )
        """)
        db.conn.commit()
        logger.info("Job runs updated with test counts")

        # Close connection after write
        db.conn.close()

        db.close()

        logger.info(f"Collection complete! Inserted {inserted_jobs} job runs and {inserted_tests} test results")
        collection_status['progress'] = f'Complete! Saved {inserted_jobs} job runs and {inserted_tests} test results'
        collection_status['error'] = None
        collection_status['completed_at'] = datetime.now().isoformat()

    except Exception as e:
        logger.error(f"Collection failed: {e}", exc_info=True)
        collection_status['error'] = str(e)
        collection_status['progress'] = 'Failed'
        collection_status['completed_at'] = None
    finally:
        logger.info("Collection thread finished")
        collection_status['running'] = False


def create_app(db_path: str, config: dict = None, config_file: str = 'config.yaml'):
    """
    Create Flask application

    Args:
        db_path: Path to SQLite database
        config: Optional Flask configuration
        config_file: Path to YAML configuration file

    Returns:
        Flask app instance
    """
    app = Flask(__name__,
                template_folder=str(Path(__file__).parent / 'templates'),
                static_folder=str(Path(__file__).parent / 'static'))

    if config:
        app.config.update(config)

    # Load tracking config for blocklist
    blocklist = []
    try:
        with open(config_file, 'r') as f:
            yaml_config = yaml.safe_load(f)
            blocklist = yaml_config.get('tracking', {}).get('blocklist', [])
    except Exception as e:
        print(f"Warning: Could not load blocklist from config: {e}")

    # Initialize database and calculator
    db = DashboardDatabase(db_path)
    calculator = MetricsCalculator(db, blocklist=blocklist)
    report_generator = WeeklyReportGenerator(db, blocklist=blocklist)

    @app.route('/')
    def index():
        """Render main dashboard page"""
        # Check if database needs data collection
        global collection_status

        # Check if database is empty or has no recent data
        try:
            # Query for recent data (last 7 days)
            recent_count = db.execute_query(
                "SELECT COUNT(*) as cnt FROM job_runs WHERE timestamp >= datetime('now', '-7 days')"
            )
            needs_collection = recent_count[0]['cnt'] == 0 if recent_count else True

            # Auto-trigger collection if needed and not already running
            if needs_collection and not collection_status['running']:
                with collection_status['lock']:
                    if not collection_status['running']:
                        collection_status['running'] = True
                        collection_status['progress'] = 'Initializing...'
                        collection_status['error'] = None

                        # Start background thread
                        thread = threading.Thread(
                            target=run_collection_background,
                            args=(db_path, config_file, 30),
                            daemon=True
                        )
                        thread.start()

        except Exception as e:
            print(f"Error checking database status: {e}")

        return render_template('dashboard.html')

    @app.route('/logs')
    def view_logs():
        """Display test logs in a new page"""
        log_content = request.args.get('content', '')
        test_name = request.args.get('test', 'Test Log')

        html = f'''
        <!DOCTYPE html>
        <html>
        <head>
            <title>{test_name} - Logs</title>
            <style>
                body {{
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    margin: 0;
                    padding: 20px;
                    background: #f8fafc;
                }}
                .container {{
                    max-width: 1200px;
                    margin: 0 auto;
                    background: white;
                    border-radius: 8px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                    overflow: hidden;
                }}
                .header {{
                    background: #1e40af;
                    color: white;
                    padding: 20px;
                    font-size: 18px;
                    font-weight: 600;
                }}
                .content {{
                    padding: 20px;
                }}
                pre {{
                    background: #1e293b;
                    color: #e2e8f0;
                    padding: 20px;
                    border-radius: 6px;
                    overflow-x: auto;
                    font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
                    font-size: 13px;
                    line-height: 1.6;
                    white-space: pre-wrap;
                    word-wrap: break-word;
                }}
                .error {{
                    color: #fca5a5;
                }}
                .info {{
                    color: #93c5fd;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">{test_name}</div>
                <div class="content">
                    <pre>{log_content}</pre>
                </div>
            </div>
        </body>
        </html>
        '''
        return html

    @app.route('/api/collection-status')
    def api_collection_status():
        """Get current collection status"""
        global collection_status
        return jsonify({
            'running': collection_status['running'],
            'progress': collection_status['progress'],
            'error': collection_status['error'],
            'completed_at': collection_status['completed_at']
        })

    @app.route('/api/trigger-collection', methods=['POST'])
    def api_trigger_collection():
        """Manually trigger data collection"""
        global collection_status

        days = request.json.get('days', 30) if request.json else 30

        with collection_status['lock']:
            if collection_status['running']:
                return jsonify({'error': 'Collection already running'}), 409

            collection_status['running'] = True
            collection_status['progress'] = 'Initializing...'
            collection_status['error'] = None
            collection_status['completed_at'] = None

            # Start background thread
            thread = threading.Thread(
                target=run_collection_background,
                args=(db_path, config_file, days),
                daemon=True
            )
            thread.start()

        return jsonify({'status': 'started'})

    @app.route('/api/summary')
    def api_summary():
        """Get summary statistics"""
        days = request.args.get('days', 7, type=int)
        version = request.args.get('version')
        platform = request.args.get('platform')
        stats = calculator.get_summary_stats(days=days, version=version, platform=platform)
        return jsonify(stats)

    @app.route('/api/trend')
    def api_trend():
        """Get overall pass rate trend"""
        days = request.args.get('days', 30, type=int)
        version = request.args.get('version')
        platform = request.args.get('platform')

        trend = calculator.get_overall_trend(
            days=days,
            version=version,
            platform=platform
        )
        return jsonify(trend)

    @app.route('/api/test-rankings')
    def api_test_rankings():
        """Get test rankings (worst performers)"""
        days = request.args.get('days', 30, type=int)
        version = request.args.get('version')
        platform = request.args.get('platform')
        limit = request.args.get('limit', 20, type=int)

        rankings = calculator.get_test_rankings(
            days=days,
            version=version,
            platform=platform,
            limit=limit
        )
        return jsonify(rankings)

    @app.route('/api/version-comparison')
    def api_version_comparison():
        """Compare pass rates across versions"""
        days = request.args.get('days', 30, type=int)
        comparison = calculator.get_version_comparison(days=days)
        return jsonify(comparison)

    @app.route('/api/platform-comparison')
    def api_platform_comparison():
        """Compare pass rates across platforms"""
        days = request.args.get('days', 30, type=int)
        version = request.args.get('version')

        comparison = calculator.get_platform_comparison(
            days=days,
            version=version
        )
        return jsonify(comparison)

    @app.route('/api/weekly-report')
    def api_weekly_report():
        """Get weekly platform breakdown report"""
        current_days = request.args.get('current_days', 7, type=int)
        previous_days = request.args.get('previous_days', 7, type=int)
        version = request.args.get('version')
        top = request.args.get('top', 10, type=int)

        # Get platform comparison
        comparison = report_generator.get_platform_week_over_week(
            current_week_days=current_days,
            previous_week_days=previous_days,
            version=version
        )

        # Get top failing tests
        top_tests = calculator.get_test_rankings(days=current_days, version=version, limit=top)

        # Get overall summary
        summary = calculator.get_summary_stats(days=current_days, version=version)

        return jsonify({
            'comparison': comparison,
            'top_tests': top_tests,
            'summary': summary
        })

    @app.route('/api/platform-tests')
    def api_platform_tests():
        """Get test results for a specific platform"""
        platform = request.args.get('platform')
        days = request.args.get('days', 7, type=int)
        version = request.args.get('version')

        if not platform:
            return jsonify({'error': 'Platform parameter is required'}), 400

        # Get test rankings for this platform
        tests = calculator.get_test_rankings(days=days, version=version, platform=platform, limit=100)

        # Get platform-specific summary
        summary = calculator.get_summary_stats(days=days, platform=platform, version=version)

        return jsonify({
            'platform': platform,
            'tests': tests,
            'summary': summary,
            'days': days
        })

    @app.teardown_appcontext
    def close_db(error):
        """Close database connection on app shutdown"""
        if error:
            print(f"App error: {error}")

    return app
