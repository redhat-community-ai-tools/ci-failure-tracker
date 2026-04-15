# Real AI Analysis Using Claude Code + MCP

This setup enables **REAL AI analysis** (not just pattern matching) using Claude Code via Model Context Protocol (MCP).

## Architecture

```
Dashboard → local_service.py → MCP Queue (SQLite) → Claude Code (YOU!)
            (HTTP)               (get/submit)         (analyzes failures)
```

## Setup

### 1. Start the MCP Server

```bash
cd /Users/rrasouli/Documents/GitHub/ci-failure-tracker/dashboard
python3 src/ai/mcp_server.py
```

This starts the MCP server that manages the analysis queue.

### 2. Start the Local AI Service

In a new terminal:

```bash
cd /Users/rrasouli/Documents/GitHub/ci-failure-tracker/dashboard
python3 src/ai/local_service.py
```

This starts the HTTP service on port 5001 that the dashboard connects to.

### 3. Configure Claude Code MCP Connection

Add this to your Claude Code MCP configuration (`~/.claude/config.json` or via settings):

```json
{
  "mcpServers": {
    "failure-analysis": {
      "command": "python3",
      "args": [
        "/Users/rrasouli/Documents/GitHub/ci-failure-tracker/dashboard/src/ai/mcp_server.py"
      ]
    }
  }
}
```

### 4. Process Analysis Requests

In this Claude Code session, tell me:

```
Process analysis requests from the MCP server
```

I will:
1. Check for pending requests using `get_pending_analysis_requests()`
2. Analyze each failure with REAL AI understanding
3. Submit results using `submit_analysis()`

## How It Works

### User Clicks "AI Analyze" Button

1. **Dashboard** sends request to `http://localhost:5001/analyze`
2. **local_service.py** adds request to MCP queue (SQLite database)
3. **Claude Code** (me) polls queue via MCP tools
4. **I analyze the failure** using:
   - Error messages
   - Build logs
   - Pattern recognition
   - Deep understanding of Windows containers, OpenShift, etc.
5. **I submit analysis** back to queue
6. **local_service.py** returns analysis to dashboard
7. **User sees REAL AI analysis**

### MCP Tools Available

**`get_pending_analysis_requests()`**
- Returns list of pending analysis requests
- Each request has: test_name, error_message, log_url, platform, version

**`submit_analysis(request_id, analysis)`**
- Submit analysis result for a request
- Analysis object must include:
  - root_cause: What caused the failure
  - component: Affected component
  - confidence: 0-100
  - failure_type: "product_bug", "system_issue", "test_bug", etc.
  - platform_specific: true/false
  - affected_platforms: ["aws", "azure", etc.]
  - evidence: Key evidence from logs
  - suggested_action: What to do next
  - issue_title: Title for Jira/GitHub issue
  - issue_description: Full issue description

## Example Analysis Loop

Tell me to run this:

```
While there are pending analysis requests:
1. Call get_pending_analysis_requests() to get the queue
2. For each request:
   - Fetch logs from log_url
   - Analyze the error message and logs
   - Determine root cause, affected component, confidence
   - Generate issue description
   - Call submit_analysis() with results
3. Wait 10 seconds and check again
```

## Testing

### Test the setup:

1. Start MCP server: `python3 src/ai/mcp_server.py`
2. Start local service: `python3 src/ai/local_service.py`
3. Click "AI Analyze" on a failing test in dashboard
4. Tell me: "Process analysis requests from MCP server"
5. Watch me analyze the failure in real-time!

### Expected Output

```
Analysis Mode: local-claude-code (FREE)
Root Cause: <REAL AI ANALYSIS>
Component: <DETERMINED BY AI>
Confidence: 85%
Evidence: <KEY LOG EXCERPTS>
Suggested Action: <ACTIONABLE FIX>
```

## Benefits

✓ **FREE** - No API costs
✓ **REAL AI** - Not pattern matching, actual analysis
✓ **Deep understanding** - Contextual awareness of Windows, OpenShift, containers
✓ **Actionable** - Specific fixes, not generic advice
✓ **Interactive** - You can ask me follow-up questions

## Troubleshooting

**"Analysis timeout"**
- Make sure MCP server is running
- Make sure you told me to process requests
- Check `/tmp/analysis_queue.db` for pending requests

**"No pending requests"**
- Click "AI Analyze" button on dashboard
- Check that local_service.py is running on port 5001
- Check dashboard is configured to use `http://localhost:5001`

**MCP connection issues**
- Verify MCP server path in config is correct
- Restart Claude Code after adding MCP configuration
- Check MCP server logs (stderr output)
