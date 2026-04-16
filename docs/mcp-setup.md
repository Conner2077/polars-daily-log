# MCP Server Setup

Polars Daily Log exposes an MCP (Model Context Protocol) server so any
MCP-compatible agent — Claude Code, OpenCode, Cursor, etc. — can query
your activities, worklogs, Jira issues, git commits, and even submit
worklogs to Jira.

## Quick install (recommended)

```bash
./pdl mcp install
```

Auto-detects the Claude Code CLI, registers the MCP server, and tells
you what to do next. To undo: `./pdl mcp uninstall`.

## Manual: `claude mcp add`

If `pdl mcp install` doesn't find your Claude Code CLI:

```bash
claude mcp add polars-daily-log --scope user -- /path/to/pdl mcp start
```

## Manual: edit `.mcp.json`

Add to `~/.claude/.mcp.json`:

```json
{
  "mcpServers": {
    "polars-daily-log": {
      "command": "/path/to/pdl",
      "args": ["mcp", "start"]
    }
  }
}
```

Then approve the server in `~/.claude/settings.json`:

```json
{
  "enabledMcpjsonServers": ["polars-daily-log"]
}
```

## After pip install (PyPI)

If you installed via `pip install auto-daily-log`, the MCP server is
available as a standalone command:

```bash
claude mcp add polars-daily-log -- polars-daily-log-mcp
```

Or with `uvx` (no install needed):

```bash
claude mcp add polars-daily-log -- uvx auto-daily-log
```

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

- "查一下我昨天的活动记录"
- "帮我把今天的工时提交到 Jira，PDL-42 算 3 小时"
- "这周我在哪些 Jira 任务上花了时间？"
- "搜一下跟 Polars 性能相关的活动"
- "昨天有哪些 git 提交？"

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Tools don't appear in Claude Code | Restart the session; check `enabledMcpjsonServers` in settings.json |
| `pdl mcp install` says "CLI not found" | Install Claude Code first, or use the manual method above |
| `submit_worklog` fails | Check Jira config in Web UI Settings (SSO login required) |
