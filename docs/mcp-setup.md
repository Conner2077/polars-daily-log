# MCP Server Setup

Polars Daily Log exposes an MCP (Model Context Protocol) server so any
MCP-compatible agent — Claude Code, OpenCode, Cursor, etc. — can query
your activities, worklogs, Jira issues, git commits, and even submit
worklogs to Jira.

## Add to Claude Code

Edit `~/.claude/settings.json` (or project-level `.claude/settings.json`):

```json
{
  "mcpServers": {
    "polars-daily-log": {
      "command": "/path/to/auto_daily_log/pdl",
      "args": ["mcp", "start"]
    }
  }
}
```

Replace `/path/to/auto_daily_log` with the actual install path.

## Available tools

| Tool | Description |
|------|-------------|
| `query_activities` | Query activities for a date (with optional keyword filter) |
| `query_worklogs` | Query worklog drafts by date / issue / status |
| `get_jira_issues` | List tracked Jira issues |
| `submit_worklog` | Submit hours to Jira (creates audit trail) |
| `generate_daily_summary` | Get existing daily worklog summary |
| `search_activities` | Full-text search across activity summaries |
| `get_git_commits` | List git commits for a date |

## Example prompts

Once connected, try these in Claude Code:

- "Query my activities for yesterday"
- "Submit 3 hours to PDL-42 for today with summary 'MCP server implementation'"
- "Search my activities for 'database'"
- "Show me my Jira issues"
