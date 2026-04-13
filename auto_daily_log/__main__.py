"""Entry point: python -m auto_daily_log"""
import os
# Clear proxy env vars FIRST — before any network library is imported
for _pv in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "no_proxy", "NO_PROXY"):
    os.environ.pop(_pv, None)

import argparse
import asyncio

from .config import load_config
from .app import Application


def main():
    parser = argparse.ArgumentParser(description="Polars Daily Log")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    parser.add_argument("--port", type=int, help="Override server port")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.port:
        config.server.port = args.port

    app = Application(config)
    asyncio.run(app.run())


if __name__ == "__main__":
    main()
