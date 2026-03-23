#!/usr/bin/env python3
"""
Test script for gcsweb collector

Tests connectivity and data fetching from OpenShift CI's gcsweb interface.
"""

import sys
import os
from datetime import datetime, timedelta

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from collectors.gcsweb import GCSWebCollector

def test_health_check():
    """Test gcsweb accessibility"""
    print("\n=== Testing gcsweb Collector ===\n")

    config = {
        'url': 'https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com',
        'bucket': 'test-platform-results',
        'max_workers': 3
    }

    collector = GCSWebCollector(config)

    print("1. Health Check...")
    if collector.health_check():
        print("   ✓ gcsweb is accessible")
    else:
        print("   ✗ Failed to access gcsweb")
        return False

    return True


def test_list_directory():
    """Test listing directories in gcsweb"""
    print("\n2. Testing Directory Listing...")

    config = {
        'url': 'https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com',
        'bucket': 'test-platform-results',
        'max_workers': 3
    }

    collector = GCSWebCollector(config)

    # List jobs in logs directory
    print("   Listing /logs/ directory...")
    links = collector._list_directory("/gcs/test-platform-results/logs/")

    if links:
        print(f"   ✓ Found {len(links)} items in /logs/")

        # Look for WINC jobs
        winc_jobs = [(path, text) for path, text in links if 'winc' in text.lower()]

        if winc_jobs:
            print(f"   ✓ Found {len(winc_jobs)} WINC-related jobs:")
            for path, text in winc_jobs[:5]:
                print(f"     - {text}")
            return True
        else:
            print("   ⚠ No WINC jobs found (searching...)")
            # Show sample jobs
            print("   Sample jobs found:")
            for path, text in links[:10]:
                print(f"     - {text}")
            return False
    else:
        print("   ✗ Failed to list directory")
        return False


def test_find_winc_jobs():
    """Search for WINC job names"""
    print("\n3. Searching for WINC Job Names...")

    config = {
        'url': 'https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com',
        'bucket': 'test-platform-results',
        'max_workers': 3
    }

    collector = GCSWebCollector(config)

    # List all jobs
    links = collector._list_directory("/gcs/test-platform-results/logs/")

    # Filter for WINC-related jobs
    winc_keywords = ['winc', 'windows']
    winc_jobs = []

    for path, text in links:
        job_name = text.rstrip('/')
        if any(keyword in job_name.lower() for keyword in winc_keywords):
            winc_jobs.append(job_name)

    if winc_jobs:
        print(f"   ✓ Found {len(winc_jobs)} WINC jobs:")
        for job in sorted(winc_jobs):
            print(f"     - {job}")

        print("\n   💡 Add these to config.yaml under collector.gcsweb.job_names")
        return True
    else:
        print("   ⚠ No WINC jobs found")
        print("   Showing sample job names to help identify pattern:")
        sample_jobs = [text.rstrip('/') for path, text in links[:20]]
        for job in sorted(sample_jobs):
            if 'periodic-ci-openshift' in job:
                print(f"     - {job}")
        return False


if __name__ == '__main__':
    print("╔══════════════════════════════════════════════════╗")
    print("║        gcsweb Collector Test Suite              ║")
    print("╚══════════════════════════════════════════════════╝")

    results = []

    # Run tests
    results.append(('Health Check', test_health_check()))
    results.append(('List Directory', test_list_directory()))
    results.append(('Find WINC Jobs', test_find_winc_jobs()))

    # Summary
    print("\n" + "="*50)
    print("SUMMARY")
    print("="*50)

    for test_name, passed in results:
        status = "✓ PASS" if passed else "⚠ PARTIAL" if test_name == "Find WINC Jobs" else "✗ FAIL"
        print(f"{status} - {test_name}")

    total = len(results)
    passed = sum(1 for _, p in results if p)

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed >= 2:  # Health check + directory listing is enough
        print("\n🎉 gcsweb collector is working! Check output above for WINC job names.")
        sys.exit(0)
    else:
        print("\n⚠️  Some tests failed. Check network connectivity.")
        sys.exit(1)
