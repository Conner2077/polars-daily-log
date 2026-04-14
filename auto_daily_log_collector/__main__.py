"""Standalone collector entry point.

Usage:
    python -m auto_daily_log_collector --config collector.yaml
"""
import argparse
import asyncio
import os
import sys

# Clear proxy env vars so HTTP pushes go direct (like the server does)
for _pv in ("http_proxy", "https_proxy", "all_proxy",
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ.pop(_pv, None)

from .config import load_config
from .runner import CollectorRuntime


def main():
    parser = argparse.ArgumentParser(description="Auto Daily Log Collector")
    parser.add_argument("--config", default="collector.yaml", help="Path to collector.yaml")
    parser.add_argument("--server", help="Override server_url from config")
    parser.add_argument(
        "--uninstall", action="store_true",
        help="Deactivate this collector on the server and remove local credentials/queue",
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)

    if args.server:
        config = config.model_copy(update={"server_url": args.server})

    if args.uninstall:
        asyncio.run(_uninstall(config))
        return

    runtime = CollectorRuntime(config)

    async def _main():
        print(f"[Collector] Registering with {config.server_url} ...")
        mid = await runtime.ensure_registered()
        print(f"[Collector] Machine ID: {mid}")
        print(f"[Collector] Platform: {runtime.adapter.platform_id()} ({runtime.adapter.platform_detail()})")
        print(f"[Collector] Capabilities: {sorted(runtime.adapter.capabilities())}")
        print(f"[Collector] Sampling every {config.interval_sec}s ...")
        try:
            await runtime.run()
        finally:
            await runtime.close()

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print("\n[Collector] Stopped by user")


async def _uninstall(config):
    """Deactivate on server, then remove local state."""
    import httpx
    from .credentials import load_credentials, clear_credentials

    creds = load_credentials(config.credentials_file)
    if not creds:
        print("[Uninstall] No local credentials found; nothing to deregister.")
    else:
        # Find collector id on server (we have machine_id but API takes numeric id)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{config.server_url.rstrip('/')}/api/collectors")
                r.raise_for_status()
                for c in r.json():
                    if c["machine_id"] == creds.machine_id:
                        d = await client.delete(
                            f"{config.server_url.rstrip('/')}/api/collectors/{c['id']}"
                        )
                        d.raise_for_status()
                        print(f"[Uninstall] Deactivated collector {creds.machine_id} on server")
                        break
                else:
                    print(f"[Uninstall] Server did not have collector {creds.machine_id}; skipping deregister")
        except Exception as e:
            print(f"[Uninstall] Server call failed: {e}")
            print("[Uninstall] Proceeding to clean local state anyway.")

    # Clear local credentials + queue
    clear_credentials(config.credentials_file)
    queue_dir = config.resolved_data_dir / "queue"
    if queue_dir.exists():
        import shutil
        shutil.rmtree(queue_dir)
    print(f"[Uninstall] Cleared local state in {config.resolved_data_dir}")


if __name__ == "__main__":
    main()
