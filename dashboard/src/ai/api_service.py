#!/usr/bin/env python3
"""
Claude Code AI Analysis API Service

This service exposes an HTTP API for submitting and processing analysis requests.
It uses the MCP queue but with an HTTP interface that Claude Code can interact with.

Usage:
    python3 src/ai/api_service.py
"""

from flask import Flask, request, jsonify, render_template_string
import sys
import os
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ai.mcp_server import AnalysisQueue

app = Flask(__name__)
queue = AnalysisQueue()


@app.route('/')
def index():
    """API documentation page"""
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Claude Code AI Analysis API</title>
        <style>
            body { font-family: system-ui; margin: 40px; background: #f5f5f5; }
            .container { max-width: 900px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; }
            h1 { color: #1e40af; }
            code { background: #f3f4f6; padding: 2px 6px; border-radius: 3px; }
            pre { background: #1e293b; color: #e2e8f0; padding: 15px; border-radius: 6px; overflow-x: auto; }
            .endpoint { border-left: 4px solid #3b82f6; padding-left: 15px; margin: 20px 0; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Claude Code AI Analysis API</h1>
            <p><strong>REAL AI</strong> failure analysis powered by Claude Code</p>

            <div class="endpoint">
                <h3>POST /analyze</h3>
                <p>Submit a test failure for AI analysis</p>
                <pre>{
  "test_name": "OCP-76765",
  "platform": "aws",
  "version": "4.22",
  "error_message": "...",
  "log_url": "https://..."
}</pre>
                <p>Returns: Analysis result or queued status</p>
            </div>

            <div class="endpoint">
                <h3>GET /queue</h3>
                <p>Get pending analysis requests (for Claude Code to process)</p>
            </div>

            <div class="endpoint">
                <h3>POST /submit</h3>
                <p>Submit analysis result for a request (used by Claude Code)</p>
                <pre>{
  "request_id": "...",
  "analysis": {
    "root_cause": "...",
    "component": "...",
    "confidence": 85,
    ...
  }
}</pre>
            </div>

            <div class="endpoint">
                <h3>GET /health</h3>
                <p>Health check endpoint</p>
            </div>
        </div>
    </body>
    </html>
    """)


@app.route('/health')
def health():
    """Health check"""
    return jsonify({
        'status': 'ok',
        'mode': 'claude-code-real-ai',
        'message': 'REAL AI analysis service - powered by Claude Code'
    })


@app.route('/analyze', methods=['POST'])
def analyze():
    """
    Submit analysis request

    Dashboard calls this endpoint. We queue the request and wait for
    Claude Code to process it.
    """
    data = request.json

    test_name = data.get('test_name')
    platform = data.get('platform')
    version = data.get('version')
    error_message = data.get('error_message', '')
    log_url = data.get('log_url', '')

    if not test_name or not platform:
        return jsonify({'error': 'Missing required fields'}), 400

    # Generate request ID
    import uuid
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

    # Poll for response (timeout after 10 seconds)
    import time
    for _ in range(10):
        response = queue.get_response(request_id)
        if response:
            # Add metadata
            response['analysis_mode'] = 'claude-code-real-ai'
            response['cost'] = 0.0
            # Remove internal fields
            response.pop('id', None)
            response.pop('request_id', None)
            response.pop('created_at', None)
            return jsonify(response)

        time.sleep(1)

    # Timeout - return queued status
    return jsonify({
        'error': 'Analysis in progress',
        'status': 'queued',
        'request_id': request_id,
        'message': 'Request queued for Claude Code analysis. Check back in a moment.'
    }), 202


@app.route('/queue', methods=['GET'])
def get_queue():
    """Get pending analysis requests - for Claude Code to process"""
    requests = queue.get_pending_requests()
    return jsonify({
        'count': len(requests),
        'requests': requests
    })


@app.route('/submit', methods=['POST'])
def submit_analysis():
    """Submit analysis result - used by Claude Code"""
    data = request.json

    request_id = data.get('request_id')
    analysis = data.get('analysis')

    if not request_id or not analysis:
        return jsonify({'error': 'Missing request_id or analysis'}), 400

    success = queue.submit_response(request_id, analysis)

    if success:
        return jsonify({
            'status': 'success',
            'request_id': request_id,
            'message': 'Analysis submitted successfully'
        })
    else:
        return jsonify({'error': 'Failed to submit analysis'}), 500


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5002))
    print("=" * 70)
    print("Claude Code AI Analysis API Service")
    print("=" * 70)
    print()
    print(f"Starting on port {port}")
    print()
    print("Endpoints:")
    print(f"  POST   /analyze - Submit analysis request")
    print(f"  GET    /queue   - Get pending requests (for Claude Code)")
    print(f"  POST   /submit  - Submit analysis result (by Claude Code)")
    print(f"  GET    /health  - Health check")
    print()
    print("This service provides REAL AI analysis powered by Claude Code")
    print("=" * 70)
    print()

    app.run(host='0.0.0.0', port=port, debug=False)
