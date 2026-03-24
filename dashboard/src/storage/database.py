"""
SQLite database for storing historical test results and metrics
"""

import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path

from collectors.base import JobRun, TestResult, TestStatus


class DashboardDatabase:
    """SQLite database for historical test data"""

    def __init__(self, db_path: str):
        """
        Initialize database connection

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # Allow SQLite to be used across threads (safe for read-mostly workloads)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row  # Return rows as dictionaries
        self._create_tables()

    def _create_tables(self):
        """Create database schema"""

        cursor = self.conn.cursor()

        # Job runs table - stores overall job statistics
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS job_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_name TEXT NOT NULL,
                build_id TEXT NOT NULL,
                status TEXT NOT NULL,
                timestamp DATETIME NOT NULL,
                duration_seconds REAL,
                version TEXT NOT NULL,
                platform TEXT NOT NULL,
                total_tests INTEGER NOT NULL,
                passed_tests INTEGER NOT NULL,
                failed_tests INTEGER NOT NULL,
                skipped_tests INTEGER NOT NULL,
                pass_rate REAL NOT NULL,
                job_url TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(job_name, build_id)
            )
        """)

        # Test results table - stores individual test results
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS test_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_name TEXT NOT NULL,
                test_description TEXT,
                status TEXT NOT NULL,
                timestamp DATETIME NOT NULL,
                duration_seconds REAL,
                error_message TEXT,
                job_name TEXT NOT NULL,
                build_id TEXT NOT NULL,
                version TEXT NOT NULL,
                platform TEXT NOT NULL,
                job_url TEXT,
                log_url TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(test_name, job_name, build_id)
            )
        """)

        # Daily metrics table - pre-aggregated daily statistics
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE NOT NULL,
                version TEXT NOT NULL,
                platform TEXT,
                total_runs INTEGER NOT NULL,
                passed_runs INTEGER NOT NULL,
                failed_runs INTEGER NOT NULL,
                total_tests INTEGER NOT NULL,
                passed_tests INTEGER NOT NULL,
                failed_tests INTEGER NOT NULL,
                overall_pass_rate REAL NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date, version, platform)
            )
        """)

        # Test metrics table - per-test aggregated statistics
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS test_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_name TEXT NOT NULL,
                date DATE NOT NULL,
                version TEXT NOT NULL,
                platform TEXT,
                total_runs INTEGER NOT NULL,
                passed_runs INTEGER NOT NULL,
                failed_runs INTEGER NOT NULL,
                pass_rate REAL NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(test_name, date, version, platform)
            )
        """)

        # Create indexes for faster queries
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_job_runs_timestamp ON job_runs(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_job_runs_version ON job_runs(version)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_test_results_timestamp ON test_results(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_test_results_test_name ON test_results(test_name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_daily_metrics_date ON daily_metrics(date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_test_metrics_test_name ON test_metrics(test_name)")

        self.conn.commit()

    def insert_job_runs(self, job_runs: List[JobRun]) -> int:
        """
        Insert job runs into database

        Args:
            job_runs: List of JobRun objects

        Returns:
            Number of rows inserted
        """
        cursor = self.conn.cursor()
        inserted = 0

        for run in job_runs:
            try:
                cursor.execute("""
                    INSERT OR REPLACE INTO job_runs (
                        job_name, build_id, status, timestamp, duration_seconds,
                        version, platform, total_tests, passed_tests, failed_tests,
                        skipped_tests, pass_rate, job_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    run.job_name,
                    run.build_id,
                    run.status.value,
                    run.timestamp.isoformat(),
                    run.duration_seconds,
                    run.version,
                    run.platform,
                    run.total_tests,
                    run.passed_tests,
                    run.failed_tests,
                    run.skipped_tests,
                    run.pass_rate,
                    run.job_url
                ))
                inserted += 1
            except sqlite3.IntegrityError:
                # Already exists, skip
                pass

        self.conn.commit()
        return inserted

    def insert_test_results(self, test_results: List[TestResult]) -> int:
        """
        Insert test results into database

        Args:
            test_results: List of TestResult objects

        Returns:
            Number of rows inserted
        """
        cursor = self.conn.cursor()
        inserted = 0

        for result in test_results:
            try:
                cursor.execute("""
                    INSERT OR REPLACE INTO test_results (
                        test_name, test_description, status, timestamp, duration_seconds, error_message,
                        job_name, build_id, version, platform, job_url, log_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    result.test_name,
                    result.test_description,
                    result.status.value,
                    result.timestamp.isoformat(),
                    result.duration_seconds,
                    result.error_message,
                    result.job_name,
                    result.build_id,
                    result.version,
                    result.platform,
                    result.job_url,
                    result.log_url
                ))
                inserted += 1
            except sqlite3.IntegrityError:
                # Already exists, skip
                pass

        self.conn.commit()
        return inserted

    def get_daily_pass_rates(
        self,
        start_date: datetime,
        end_date: datetime,
        version: Optional[str] = None,
        platform: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get daily pass rates within date range

        Args:
            start_date: Start date
            end_date: End date
            version: Optional version filter
            platform: Optional platform filter

        Returns:
            List of daily metrics dictionaries
        """
        cursor = self.conn.cursor()

        query = """
            SELECT
                DATE(timestamp) as date,
                version,
                platform,
                COUNT(*) as total_runs,
                SUM(CASE WHEN status = 'passed' THEN 1 ELSE 0 END) as passed_runs,
                CAST(SUM(passed_tests) AS REAL) / SUM(total_tests) * 100 as avg_pass_rate
            FROM job_runs
            WHERE timestamp >= ? AND timestamp <= ?
            AND total_tests >= 10
        """

        params = [start_date.isoformat(), end_date.isoformat()]

        if version:
            query += " AND version = ?"
            params.append(version)

        if platform:
            query += " AND platform = ?"
            params.append(platform)

        query += " GROUP BY DATE(timestamp), version, platform ORDER BY date"

        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def get_test_pass_rates(
        self,
        start_date: datetime,
        end_date: datetime,
        test_name: Optional[str] = None,
        version: Optional[str] = None,
        platform: Optional[str] = None,
        blocklist: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get per-test pass rates

        Args:
            start_date: Start date
            end_date: End date
            test_name: Optional test name filter
            version: Optional version filter
            platform: Optional platform filter
            blocklist: Optional list of test names to exclude

        Returns:
            List of test metrics dictionaries
        """
        cursor = self.conn.cursor()

        query = """
            SELECT
                test_name,
                MAX(test_description) as test_description,
                version,
                COUNT(*) as total_runs,
                SUM(CASE WHEN status = 'passed' THEN 1 ELSE 0 END) as passed_runs,
                CAST(SUM(CASE WHEN status = 'passed' THEN 1 ELSE 0 END) AS REAL) / COUNT(*) * 100 as pass_rate
            FROM test_results
            WHERE timestamp >= ? AND timestamp <= ?
            AND status != 'skipped'
            AND test_name LIKE 'OCP-%'
        """

        params = [start_date.isoformat(), end_date.isoformat()]

        if test_name:
            query += " AND test_name = ?"
            params.append(test_name)

        if version:
            query += " AND version = ?"
            params.append(version)

        if platform:
            query += " AND platform = ?"
            params.append(platform)

        if blocklist:
            placeholders = ','.join(['?' for _ in blocklist])
            query += f" AND test_name NOT IN ({placeholders})"
            params.extend(blocklist)

        query += " GROUP BY test_name, version ORDER BY pass_rate ASC"

        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def get_version_comparison(
        self,
        start_date: datetime,
        end_date: datetime
    ) -> List[Dict[str, Any]]:
        """
        Compare pass rates across versions

        Args:
            start_date: Start date
            end_date: End date

        Returns:
            List of version comparison dictionaries
        """
        cursor = self.conn.cursor()

        query = """
            SELECT
                version,
                COUNT(*) as total_runs,
                SUM(CASE WHEN status = 'passed' THEN 1 ELSE 0 END) as passed_runs,
                CAST(SUM(passed_tests) AS REAL) / SUM(total_tests) * 100 as avg_pass_rate,
                AVG(total_tests) as avg_total_tests
            FROM job_runs
            WHERE timestamp >= ? AND timestamp <= ?
            AND total_tests >= 10
            GROUP BY version
            ORDER BY version
        """

        cursor.execute(query, [start_date.isoformat(), end_date.isoformat()])
        return [dict(row) for row in cursor.fetchall()]

    def execute_query(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """
        Execute a raw SQL query and return results

        Args:
            query: SQL query string
            params: Query parameters tuple

        Returns:
            List of result rows as dictionaries
        """
        cursor = self.conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def close(self):
        """Close database connection"""
        self.conn.close()
