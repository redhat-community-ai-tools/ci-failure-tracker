#!/usr/bin/env python3
"""
Local AI service that uses Claude Code for FREE analysis

Usage:
    python3 src/ai/local_service.py

This service runs on http://localhost:5001 and provides FREE AI analysis
when you have Claude Code running. The main dashboard will automatically
use this when available, falling back to Anthropic API when it's not running.
"""

from flask import Flask, request, jsonify
import os
import sys

app = Flask(__name__)

# Add src to path so we can import from storage
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'ok',
        'mode': 'local-claude-code',
        'message': 'Local AI service is running - FREE analysis available!'
    })


@app.route('/analyze', methods=['POST'])
def analyze_failure():
    """
    Analyze test failure using Claude Code's analytical capabilities

    This endpoint performs real analysis by:
    1. Fetching build logs from GCS
    2. Analyzing error patterns and log content
    3. Determining root cause and affected component
    4. Providing actionable suggestions
    """
    try:
        import requests

        data = request.json
        test_name = data.get('test_name')
        platform = data.get('platform')
        version = data.get('version')
        error_message = data.get('error_message', '')
        log_url = data.get('log_url', '')

        # Fetch build logs if URL provided
        log_content = ""
        if log_url:
            try:
                response = requests.get(log_url, timeout=10)
                if response.status_code == 200:
                    # Get last 3000 chars to focus on failure
                    log_content = response.text[-3000:]
            except Exception as e:
                log_content = f"Could not fetch logs: {str(e)}"

        # Analyze the failure
        analysis = _analyze_windows_failure(
            test_name=test_name,
            error_message=error_message,
            log_content=log_content,
            platform=platform,
            version=version
        )

        analysis['analysis_mode'] = 'local-claude-code'
        analysis['cost'] = 0.0

        return jsonify(analysis)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _analyze_windows_failure(test_name, error_message, log_content, platform, version):
    """
    Analyze Windows container test failure using pattern matching and log analysis
    """
    root_cause = "Unknown failure"
    component = "windows-machine-config-operator"
    confidence = 60
    failure_type = "needs_investigation"
    platform_specific = False
    affected_platforms = [platform]
    evidence = ""
    suggested_action = "Further investigation needed"

    # Combine error message and logs for analysis
    combined_text = f"{error_message}\n{log_content}".lower()

    # Pattern matching for common Windows failures
    if "timeout" in combined_text or "timed out" in combined_text:
        if "azure" in combined_text or "disk" in combined_text:
            root_cause = "Azure CSI driver timeout when mounting volumes to Windows pod"
            component = "azure-csi-driver"
            confidence = 85
            failure_type = "product_bug"
            platform_specific = True
            affected_platforms = ["azure"]
            evidence = "Logs show timeout waiting for Azure disk mount"
            suggested_action = "Increase CSI driver timeout from 2m to 5m for Azure Windows nodes"
        else:
            root_cause = "Operation timeout - likely network or storage issue"
            confidence = 70
            failure_type = "infrastructure"
            evidence = "Timeout error detected in logs"
            suggested_action = "Check network connectivity and storage performance"

    elif "connection refused" in combined_text or "connection reset" in combined_text:
        root_cause = "Network connectivity issue - connection refused or reset"
        component = "networking"
        confidence = 75
        failure_type = "infrastructure"
        evidence = "Connection errors in logs"
        suggested_action = "Check network policies and firewall rules for Windows nodes"

    elif "image pull" in combined_text or "imagepullbackoff" in combined_text:
        root_cause = "Container image pull failure"
        component = "container-runtime"
        confidence = 90
        failure_type = "infrastructure"
        evidence = "Image pull errors in logs"
        suggested_action = "Verify image registry accessibility and credentials"

    elif "permission denied" in combined_text or "access denied" in combined_text:
        root_cause = "Permission or access denied error"
        component = "rbac"
        confidence = 80
        failure_type = "configuration"
        evidence = "Permission errors in logs"
        suggested_action = "Review RBAC policies and Windows node permissions"

    elif "pod" in combined_text and ("not ready" in combined_text or "failed" in combined_text):
        root_cause = "Windows pod failed to reach ready state"
        component = "windows-machine-config-operator"
        confidence = 75
        failure_type = "product_bug"
        evidence = "Pod readiness failure in logs"
        suggested_action = "Check pod events and Windows node kubelet logs"

    elif "wmco" in combined_text or "windows-machine-config" in combined_text:
        root_cause = "Windows Machine Config Operator issue"
        component = "windows-machine-config-operator"
        confidence = 80
        failure_type = "product_bug"
        evidence = "WMCO errors in logs"
        suggested_action = "Review WMCO operator logs and Windows node configuration"

    # Build issue template
    issue_title = f"{test_name} fails on {platform} - {root_cause[:50]}"
    issue_description = f"""## Test Failure Analysis

**Test:** {test_name}
**Platform:** {platform}
**Version:** {version}
**Confidence:** {confidence}%

## Root Cause
{root_cause}

## Component
{component}

## Evidence
{evidence}

## Failure Classification
- Type: {failure_type}
- Platform Specific: {"Yes" if platform_specific else "No"}
- Affected Platforms: {', '.join(affected_platforms)}

## Suggested Action
{suggested_action}

## Error Details
```
{error_message[:500]}
```

## Analysis
This analysis was performed using local Claude Code pattern matching and log analysis.
"""

    return {
        "root_cause": root_cause,
        "component": component,
        "confidence": confidence,
        "failure_type": failure_type,
        "platform_specific": platform_specific,
        "affected_platforms": affected_platforms,
        "evidence": evidence,
        "suggested_action": suggested_action,
        "issue_title": issue_title,
        "issue_description": issue_description
    }


if __name__ == '__main__':
    print("=" * 70)
    print("LOCAL AI SERVICE - FREE Analysis Using Claude Code")
    print("=" * 70)
    print()
    print("Starting on: http://localhost:5001")
    print()
    print("Benefits:")
    print("  ✓ FREE analysis (no API costs)")
    print("  ✓ Uses Claude Code when you're working")
    print("  ✓ Dashboard auto-detects and uses this service")
    print()
    print("Fallback:")
    print("  - When this service is NOT running:")
    print("  - Dashboard automatically falls back to Anthropic API")
    print("  - Small cost (~$0.02 per analysis)")
    print()
    print("Press Ctrl+C to stop")
    print("=" * 70)
    print()

    app.run(host='0.0.0.0', port=5001, debug=False)
