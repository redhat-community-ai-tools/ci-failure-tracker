#!/usr/bin/env python3
"""
Test script for authenticated access to qe-private-deck Prow Deck

This script tests if we can access the private Prow Deck with session cookies.
"""

import requests
from datetime import datetime

# Session cookie from browser
COOKIE = "_oauth_proxy=cnJhc291bGlAY2x1c3Rlci5sb2NhbA==|1774293066|Gw0T42oaRfYcxJIG4Hx5RZDqv9Y="

# Base URLs
PRIVATE_DECK_BASE = "https://qe-private-deck-ci.apps.ci.l2s4.p1.openshiftapps.com"
GCSWEB_BASE = "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com"

def test_authentication():
    """Test if cookie works for authentication"""
    session = requests.Session()
    session.headers.update({
        'Cookie': COOKIE,
        'User-Agent': 'Mozilla/5.0'
    })

    # Test homepage
    print("Testing authentication...")
    response = session.get(f"{PRIVATE_DECK_BASE}/", timeout=10)

    if response.status_code == 200 and "Prow Status" in response.text:
        print("[OK] Authentication successful!")
        return session
    else:
        print(f"[FAIL] Authentication failed: {response.status_code}")
        return None

def explore_bucket_structure(session):
    """Explore what's available in the qe-private-deck bucket"""
    print("\n" + "="*60)
    print("Exploring qe-private-deck bucket structure...")
    print("="*60)

    paths_to_try = [
        "/view/gs/qe-private-deck/",
        "/view/gs/qe-private-deck/logs/",
        "/view/gs/qe-private-deck/pr-logs/",
    ]

    for path in paths_to_try:
        url = f"{PRIVATE_DECK_BASE}{path}"
        print(f"\nTrying: {url}")

        try:
            response = session.get(url, timeout=10)

            if response.status_code == 200:
                # Check if it's a Spyglass page
                if "spyglass" in response.text.lower():
                    print(f"  [OK] Accessible (Spyglass view)")

                    # Try to find artifact links
                    if "Artifacts" in response.text:
                        print("  [INFO] Has artifacts link")
                else:
                    print(f"  [OK] Accessible ({len(response.text)} bytes)")
            else:
                print(f"  [FAIL] Status: {response.status_code}")

        except Exception as e:
            print(f"  [ERROR] {e}")

def search_for_winc_jobs(session):
    """Try to find WINC periodic jobs"""
    print("\n" + "="*60)
    print("Searching for WINC periodic jobs...")
    print("="*60)

    # Try different job name patterns
    job_patterns = [
        "periodic-ci-openshift-openshift-tests-private-release-4.22-amd64-nightly-aws-ipi-ovn-winc-f7",
        "periodic-ci-openshift-openshift-tests-private-release-4.21-amd64-nightly-aws-ipi-ovn-winc-f7",
    ]

    for job_name in job_patterns:
        url = f"{PRIVATE_DECK_BASE}/view/gs/qe-private-deck/logs/{job_name}"
        print(f"\nTrying: {job_name}")

        try:
            response = session.get(url, timeout=10)

            if response.status_code == 200:
                if len(response.text) > 1000:  # Has content
                    print(f"  [OK] Found! ({len(response.text)} bytes)")
                    # Try to parse build IDs
                    if "artifact-link" in response.text:
                        print("  [INFO] Contains build directories")
                else:
                    print(f"  [WARN] Empty or redirected ({len(response.text)} bytes)")
            else:
                print(f"  [FAIL] Status: {response.status_code}")

        except Exception as e:
            print(f"  [ERROR] {e}")

def main():
    """Main test function"""
    print("="*60)
    print("Private Deck Authentication Test")
    print("="*60)

    # Test authentication
    session = test_authentication()

    if not session:
        print("\n[FAIL] Cannot proceed without authentication")
        return

    # Explore bucket structure
    explore_bucket_structure(session)

    # Search for WINC jobs
    search_for_winc_jobs(session)

    print("\n" + "="*60)
    print("Test completed!")
    print("="*60)

if __name__ == "__main__":
    main()
