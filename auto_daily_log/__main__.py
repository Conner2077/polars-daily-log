"""Entry point: python -m auto_daily_log"""
import argparse
import asyncio

from .config import load_config
from .app import Application


def main():
    parser = argparse.ArgumentParser(description="Auto Daily Log")
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
