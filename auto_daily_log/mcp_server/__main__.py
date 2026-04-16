"""Entry point: python -m auto_daily_log.mcp_server"""
from .server import mcp

if __name__ == "__main__":
    mcp.run(transport="stdio")
