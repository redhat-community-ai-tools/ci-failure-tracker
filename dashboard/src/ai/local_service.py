#!/usr/bin/env python3
"""
Local AI service that uses Claude Code via MCP for FREE REAL AI analysis

Usage:
    # Terminal 1: Start MCP server
    python3 src/ai/mcp_server.py

    # Terminal 2: Start local service
    python3 src/ai/local_service.py

    # Terminal 3: Tell Claude Code to process requests
    # In Claude Code session, say:
    # "Process analysis requests from the MCP server"

This service runs on http://localhost:5001 and provides FREE REAL AI analysis
when you have Claude Code connected and processing the queue.
"""

from flask import Flask, request, jsonify
import os
import sys
import time
import uuid

app = Flask(__name__)

# Add src to path so we can import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Import the analysis queue
from ai.mcp_server import AnalysisQueue

queue = AnalysisQueue()


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'ok',
        'mode': 'local-claude-code-mcp',
        'message': 'Local AI service is running - Connect Claude Code to MCP server for REAL AI!'
    })


@app.route('/analyze', methods=['POST'])
def analyze_failure():
    """
    Analyze test failure using Claude Code via MCP

    Flow:
    1. Add request to MCP queue
    2. Wait for Claude Code to process it
    3. Return REAL AI analysis result
    """
    try:
        data = request.json
        test_name = data.get('test_name')
        platform = data.get('platform')
        version = data.get('version')
        error_message = data.get('error_message', '')
        log_url = data.get('log_url', '')

        # Generate unique request ID
        request_id = str(uuid.uuid4())

        # Add to queue
        success = queue.add_request(
            request_id=request_id,
            test_name=test_name,
            error_message=error_message,
            log_url=log_url,
            platform=platform,
            version=version
        )

        if not success:
            return jsonify({'error': 'Failed to queue request'}), 500

        # Poll for response (timeout after 60 seconds)
        for _ in range(60):
            response = queue.get_response(request_id)
            if response:
                # Add metadata
                response['analysis_mode'] = 'local-claude-code'
                response['cost'] = 0.0
                # Remove internal fields
                response.pop('id', None)
                response.pop('request_id', None)
                response.pop('created_at', None)
                return jsonify(response)

            time.sleep(1)

        # Timeout - Claude Code didn't process request
        return jsonify({
            'error': 'Analysis timeout - is Claude Code processing requests?',
            'hint': 'Tell Claude Code: "Process analysis requests from MCP server"'
        }), 504

    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    print("=" * 70)
    print("LOCAL AI SERVICE - REAL AI Analysis Using Claude Code + MCP")
    print("=" * 70)
    print()
    print("Starting on: http://localhost:5001")
    print()
    print("Setup:")
    print("  1. Make sure MCP server is running:")
    print("     python3 src/ai/mcp_server.py")
    print()
    print("  2. Tell Claude Code to process requests:")
    print("     'Process analysis requests from the MCP server'")
    print()
    print("  3. Click 'AI Analyze' on dashboard - gets REAL AI analysis!")
    print()
    print("Benefits:")
    print("  ✓ FREE analysis (no API costs)")
    print("  ✓ REAL AI using Claude Code (not just pattern matching)")
    print("  ✓ Deep understanding of failures")
    print("  ✓ Dashboard auto-detects and uses this service")
    print()
    print("Press Ctrl+C to stop")
    print("=" * 70)
    print()

    app.run(host='0.0.0.0', port=5001, debug=False)
