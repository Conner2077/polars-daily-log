"""MCP (Model Context Protocol) server for Polars Daily Log.

Exposes activities, worklogs, Jira issues, git commits, and submission
tools over stdio transport so any MCP-compatible agent can interact
with the local data.
"""


def main():
    """Console-scripts entry point: `polars-daily-log-mcp`."""
    from .server import mcp
    mcp.run(transport="stdio")
