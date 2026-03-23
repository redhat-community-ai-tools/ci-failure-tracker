#!/usr/bin/env python3
"""
Quick test script for Prow GCS collector

Tests connectivity and basic data fetching from GCS buckets.
"""

import sys
import os
from datetime import datetime, timedelta

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from collectors.prow_gcs import ProwGCSCollector

def test_health_check():
    """Test GCS bucket accessibility"""
    print("\n=== Testing Prow GCS Collector ===\n")

    config = {
        'bucket': 'origin-ci-test',
        'max_workers': 3
    }

    collector = ProwGCSCollector(config)

    print("1. Health Check...")
    if collector.health_check():
        print("   ✓ GCS bucket is accessible")
    else:
        print("   ✗ Failed to access GCS bucket")
        return False

    return True


def test_list_job_runs():
    """Test listing job runs"""
    print("\n2. Listing Recent Job Runs...")

    config = {
        'bucket': 'origin-ci-test',
        'max_workers': 3
    }

    collector = ProwGCSCollector(config)

    # Test with a known WINC job
    job_name = "periodic-ci-openshift-openshift-tests-private-release-4.22-amd64-aws-winc-e2e"
    end_date = datetime.now()
    start_date = end_date - timedelta(days=7)

    print(f"   Job: {job_name}")
    print(f"   Date range: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")

    runs = collector._list_job_runs(job_name, start_date, end_date, max_results=5)

    if runs:
        print(f"   ✓ Found {len(runs)} recent builds:")
        for run in runs[:3]:
            print(f"     - Build ID: {run['build_id']}")
            if run['timestamp']:
                print(f"       Timestamp: {run['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}")
    else:
        print("   ⚠ No builds found (job might not exist or no recent runs)")

    return len(runs) > 0


def test_fetch_finished_json():
    """Test fetching finished.json"""
    print("\n3. Fetching Job Metadata (finished.json)...")

    config = {
        'bucket': 'origin-ci-test',
        'max_workers': 3
    }

    collector = ProwGCSCollector(config)

    # Get a recent build
    job_name = "periodic-ci-openshift-openshift-tests-private-release-4.22-amd64-aws-winc-e2e"
    end_date = datetime.now()
    start_date = end_date - timedelta(days=7)

    runs = collector._list_job_runs(job_name, start_date, end_date, max_results=1)

    if not runs:
        print("   ⚠ No recent builds to test")
        return False

    run = runs[0]
    print(f"   Testing with build: {run['build_id']}")

    finished = collector._fetch_finished_json(run['path'])

    if finished:
        print(f"   ✓ Successfully fetched finished.json")
        print(f"     Result: {finished.get('result', 'UNKNOWN')}")
        print(f"     Duration: {finished.get('duration', 0)} seconds")
        return True
    else:
        print("   ✗ Failed to fetch finished.json")
        return False


if __name__ == '__main__':
    print("╔══════════════════════════════════════════════════╗")
    print("║      Prow GCS Collector Test Suite              ║")
    print("╚══════════════════════════════════════════════════╝")

    results = []

    # Run tests
    results.append(('Health Check', test_health_check()))
    results.append(('List Job Runs', test_list_job_runs()))
    results.append(('Fetch Metadata', test_fetch_finished_json()))

    # Summary
    print("\n" + "="*50)
    print("SUMMARY")
    print("="*50)

    for test_name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status} - {test_name}")

    total = len(results)
    passed = sum(1 for _, p in results if p)

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 All tests passed! Prow GCS collector is working.")
        sys.exit(0)
    else:
        print("\n⚠️  Some tests failed. Check configuration or network connectivity.")
        sys.exit(1)
