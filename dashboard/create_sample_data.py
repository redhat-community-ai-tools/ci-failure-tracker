#!/usr/bin/env python3
"""
Create sample data for dashboard demo

Generates realistic test data so you can see the dashboard in action
without needing ReportPortal access.
"""

import sys
import os
import random
from datetime import datetime, timedelta

sys.path.insert(0, 'src')

from collectors.base import JobRun, TestResult, TestStatus
from storage.database import DashboardDatabase

def generate_sample_data():
    """Generate sample test data for last 14 days"""

    print("Creating sample data for dashboard demo...")

    # Create database
    db = DashboardDatabase("./data/dashboard.db")

    # Sample test names
    test_names = [
        "OCP-11111", "OCP-22222", "OCP-33333", "OCP-44444",
        "OCP-55555", "OCP-66666", "OCP-77777", "OCP-88888",
        "OCP-99999", "OCP-10101"
    ]

    versions = ["4.21", "4.22"]
    platforms = ["aws", "gcp", "azure", "nutanix"]

    job_runs = []
    test_results = []

    # Generate 14 days of data
    for days_ago in range(14):
        date = datetime.now() - timedelta(days=days_ago)

        for version in versions:
            for platform in platforms:
                # Generate 2-3 job runs per day per version/platform
                for run_num in range(random.randint(2, 3)):
                    job_name = f"periodic-ci-openshift-openshift-tests-private-release-{version}-amd64-{platform}-winc-e2e"
                    build_id = f"{int(date.timestamp())}{run_num}"

                    # Random pass rate between 75-95%
                    base_pass_rate = random.uniform(0.75, 0.95)

                    # Version 4.22 slightly better than 4.21
                    if version == "4.22":
                        base_pass_rate += 0.05

                    total_tests = random.randint(80, 120)
                    passed = int(total_tests * base_pass_rate)
                    failed = total_tests - passed

                    job_run = JobRun(
                        job_name=job_name,
                        build_id=build_id,
                        status=TestStatus.PASSED if failed < 5 else TestStatus.FAILED,
                        timestamp=date,
                        duration_seconds=random.randint(1200, 2400),
                        version=version,
                        platform=platform,
                        total_tests=total_tests,
                        passed_tests=passed,
                        failed_tests=failed,
                        skipped_tests=0,
                        job_url=f"https://prow.ci.openshift.org/view/test/{build_id}"
                    )
                    job_runs.append(job_run)

                    # Generate individual test results
                    for test_name in random.sample(test_names, random.randint(8, 10)):
                        # Some tests are more flaky than others
                        if test_name in ["OCP-33333", "OCP-77777"]:
                            pass_probability = 0.6  # Flaky tests
                        else:
                            pass_probability = base_pass_rate

                        status = TestStatus.PASSED if random.random() < pass_probability else TestStatus.FAILED

                        test_result = TestResult(
                            test_name=test_name,
                            status=status,
                            timestamp=date,
                            duration_seconds=random.uniform(10, 120),
                            error_message="Test failed: assertion error" if status == TestStatus.FAILED else None,
                            job_name=job_name,
                            build_id=build_id,
                            version=version,
                            platform=platform,
                            job_url=f"https://prow.ci.openshift.org/view/test/{build_id}",
                            log_url=f"https://prow.ci.openshift.org/view/test/{build_id}/logs"
                        )
                        test_results.append(test_result)

    # Insert data
    print(f"Inserting {len(job_runs)} job runs...")
    db.insert_job_runs(job_runs)

    print(f"Inserting {len(test_results)} test results...")
    db.insert_test_results(test_results)

    db.close()

    print("\n✅ Sample data created successfully!")
    print(f"   - {len(job_runs)} job runs")
    print(f"   - {len(test_results)} test results")
    print(f"   - Covers last 14 days")
    print(f"   - Versions: {', '.join(versions)}")
    print(f"   - Platforms: {', '.join(platforms)}")
    print("\nNow run: ./dashboard.py serve")

if __name__ == '__main__':
    generate_sample_data()
