#!/bin/bash
# Script to find WINC job names in Prow

echo "Searching for WINC job names in Prow..."
echo "This may take a moment..."
echo ""

# Method 1: Check Prow's configured jobs
echo "=== Checking Prow Configured Jobs ==="
curl -sL "https://prow.ci.openshift.org/prowjobs.js" 2>/dev/null | \
  grep -i "winc\|windows" | \
  grep -o '"periodic-ci[^"]*"' | \
  sed 's/"//g' | \
  sort -u | \
  head -20

echo ""
echo "=== Alternative: Check your existing ci_failure_tracker runs ==="
echo "Look at ../teams/winc.yaml for job patterns:"
grep "job_patterns:" -A 2 ../teams/winc.yaml

echo ""
echo "=== Recommended: Ask team for exact job names ==="
echo "Message to send:"
echo "---"
echo "Hi team, for the new test pass rate dashboard, I need the exact Prow job names for WINC tests."
echo "Examples:"
echo "  - periodic-ci-openshift-openshift-tests-private-release-4.21-amd64-aws-winc-e2e"
echo "  - periodic-ci-openshift-windows-machine-config-operator-release-4.22-..."
echo ""
echo "Can you share the complete list of WINC periodic job names?"
echo "---"
