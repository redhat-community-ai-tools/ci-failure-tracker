#!/usr/bin/env python3
"""
MCP Server for AI Failure Analysis

This MCP server allows Claude Code to analyze test failures in real-time.

Architecture:
1. local_service.py writes analysis requests to queue
2. Claude Code connects to this MCP server
3. Claude Code calls get_pending_requests() to see what needs analysis
4. Claude Code analyzes failures and calls submit_analysis() with results
5. local_service.py polls for completed analyses

Usage:
    python3 src/ai/mcp_server.py
"""

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


class AnalysisQueue:
    """SQLite-based queue for analysis requests and responses"""

    def __init__(self, db_path: str = "/tmp/analysis_queue.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize database tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS analysis_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT UNIQUE NOT NULL,
                test_name TEXT NOT NULL,
                error_message TEXT,
                log_url TEXT,
                platform TEXT,
                version TEXT,
                status TEXT DEFAULT 'pending',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS analysis_responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT UNIQUE NOT NULL,
                root_cause TEXT,
                component TEXT,
                confidence INTEGER,
                failure_type TEXT,
                platform_specific INTEGER,
                affected_platforms TEXT,
                evidence TEXT,
                suggested_action TEXT,
                issue_title TEXT,
                issue_description TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (request_id) REFERENCES analysis_requests(request_id)
            )
        """)

        conn.commit()
        conn.close()

    def add_request(self, request_id: str, test_name: str, error_message: str,
                   log_url: str, platform: str, version: str) -> bool:
        """Add a new analysis request to the queue"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO analysis_requests
                (request_id, test_name, error_message, log_url, platform, version)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (request_id, test_name, error_message, log_url, platform, version))

            conn.commit()
            conn.close()
            return True
        except sqlite3.IntegrityError:
            # Request already exists
            return False

    def get_pending_requests(self) -> list[dict[str, Any]]:
        """Get all pending analysis requests"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM analysis_requests
            WHERE status = 'pending'
            ORDER BY created_at ASC
        """)

        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def submit_response(self, request_id: str, analysis: dict[str, Any]) -> bool:
        """Submit analysis response for a request"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Insert response
            cursor.execute("""
                INSERT INTO analysis_responses
                (request_id, root_cause, component, confidence, failure_type,
                 platform_specific, affected_platforms, evidence, suggested_action,
                 issue_title, issue_description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                request_id,
                analysis.get('root_cause'),
                analysis.get('component'),
                analysis.get('confidence'),
                analysis.get('failure_type'),
                1 if analysis.get('platform_specific') else 0,
                ','.join(analysis.get('affected_platforms', [])),
                analysis.get('evidence'),
                analysis.get('suggested_action'),
                analysis.get('issue_title'),
                analysis.get('issue_description')
            ))

            # Mark request as completed
            cursor.execute("""
                UPDATE analysis_requests
                SET status = 'completed', completed_at = ?
                WHERE request_id = ?
            """, (datetime.now().isoformat(), request_id))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"Error submitting response: {e}", file=sys.stderr)
            return False

    def get_response(self, request_id: str) -> dict[str, Any] | None:
        """Get analysis response for a request"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM analysis_responses
            WHERE request_id = ?
        """, (request_id,))

        row = cursor.fetchone()
        conn.close()

        if row:
            result = dict(row)
            # Convert platform_specific back to boolean
            result['platform_specific'] = bool(result['platform_specific'])
            # Convert affected_platforms back to list
            if result['affected_platforms']:
                result['affected_platforms'] = result['affected_platforms'].split(',')
            return result
        return None


# Initialize queue
queue = AnalysisQueue()


def handle_get_pending_requests(arguments: dict) -> dict:
    """MCP tool: Get pending analysis requests"""
    requests = queue.get_pending_requests()
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(requests, indent=2)
            }
        ]
    }


def handle_submit_analysis(arguments: dict) -> dict:
    """MCP tool: Submit analysis result"""
    request_id = arguments.get("request_id")
    analysis = arguments.get("analysis")

    if not request_id or not analysis:
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"error": "Missing request_id or analysis"})
                }
            ],
            "isError": True
        }

    # Parse analysis if it's a JSON string
    if isinstance(analysis, str):
        try:
            analysis = json.loads(analysis)
        except json.JSONDecodeError:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"error": "Invalid analysis JSON"})
                    }
                ],
                "isError": True
            }

    success = queue.submit_response(request_id, analysis)

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps({
                    "success": success,
                    "request_id": request_id
                })
            }
        ]
    }


def main():
    """Run MCP server using stdio transport"""
    # MCP servers communicate via stdin/stdout using JSON-RPC

    for line in sys.stdin:
        try:
            request = json.loads(line)

            method = request.get("method")
            params = request.get("params", {})
            request_id = request.get("id")

            if method == "tools/list":
                # List available tools
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "tools": [
                            {
                                "name": "get_pending_analysis_requests",
                                "description": "Get list of test failures waiting for AI analysis. Returns pending analysis requests with test name, error messages, logs, platform, and version.",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {},
                                    "required": []
                                }
                            },
                            {
                                "name": "submit_analysis",
                                "description": "Submit AI analysis result for a test failure. Provide the request_id and analysis object with root_cause, component, confidence, failure_type, evidence, suggested_action, and issue details.",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "request_id": {
                                            "type": "string",
                                            "description": "The request ID from get_pending_analysis_requests"
                                        },
                                        "analysis": {
                                            "type": "object",
                                            "description": "Analysis result object",
                                            "properties": {
                                                "root_cause": {"type": "string"},
                                                "component": {"type": "string"},
                                                "confidence": {"type": "integer"},
                                                "failure_type": {"type": "string"},
                                                "platform_specific": {"type": "boolean"},
                                                "affected_platforms": {"type": "array"},
                                                "evidence": {"type": "string"},
                                                "suggested_action": {"type": "string"},
                                                "issue_title": {"type": "string"},
                                                "issue_description": {"type": "string"}
                                            }
                                        }
                                    },
                                    "required": ["request_id", "analysis"]
                                }
                            }
                        ]
                    }
                }

            elif method == "tools/call":
                tool_name = params.get("name")
                arguments = params.get("arguments", {})

                if tool_name == "get_pending_analysis_requests":
                    result = handle_get_pending_requests(arguments)
                elif tool_name == "submit_analysis":
                    result = handle_submit_analysis(arguments)
                else:
                    result = {
                        "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                        "isError": True
                    }

                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": result
                }

            else:
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {method}"
                    }
                }

            print(json.dumps(response), flush=True)

        except json.JSONDecodeError:
            error_response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": "Parse error"
                }
            }
            print(json.dumps(error_response), flush=True)
        except Exception as e:
            error_response = {
                "jsonrpc": "2.0",
                "id": request.get("id") if 'request' in locals() else None,
                "error": {
                    "code": -32603,
                    "message": f"Internal error: {str(e)}"
                }
            }
            print(json.dumps(error_response), flush=True)


if __name__ == "__main__":
    print("MCP Server for AI Failure Analysis starting...", file=sys.stderr)
    print("Waiting for requests on stdin...", file=sys.stderr)
    main()
