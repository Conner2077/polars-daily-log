"""CLI entry: ``python -m auto_daily_log.updater <subcommand>``.

Subcommands:
  check                       — query GitHub, print latest version JSON
  apply --target X --wheel U  — full upgrade (stops/restarts server)
  rollback --backup ID        — restore an earlier version + DB snapshot
  list-backups                — JSON list of all snapshots, newest first
  prune [--keep N]            — delete old backups beyond ``--keep`` (default 3)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .. import __version__
from . import backup as backup_mod
from . import version_check
from .runner import RestartSpec, apply_update, rollback


def _restart_spec_from_args(args: argparse.Namespace) -> RestartSpec:
    return RestartSpec(
        argv=args.restart_argv.split("\x1f") if args.restart_argv else [],
        cwd=args.restart_cwd or os.getcwd(),
        log_path=args.restart_log or "",
        pidfile=args.restart_pidfile or "",
        health_url=args.health_url,
        wait_seconds=args.wait_seconds,
    )


def _config_paths(args: argparse.Namespace) -> list[Path]:
    raw = (args.config_paths or "").strip()
    if not raw:
        return []
    return [Path(p) for p in raw.split(os.pathsep) if p]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m auto_daily_log.updater")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="query GitHub for the latest release")
    p_check.add_argument("--force", action="store_true", help="bypass the 24h cache")

    p_apply = sub.add_parser("apply", help="run the full upgrade")
    p_apply.add_argument("--target-version", required=True)
    p_apply.add_argument("--wheel-url", required=True)
    p_apply.add_argument("--server-pid", type=int, default=None)
    p_apply.add_argument("--restart-argv", default="",
                         help="\\x1f-separated argv for restarting the server")
    p_apply.add_argument("--restart-cwd", default="")
    p_apply.add_argument("--restart-log", default="")
    p_apply.add_argument("--restart-pidfile", default="")
    p_apply.add_argument("--health-url", default="http://127.0.0.1:8888/api/dashboard/today")
    p_apply.add_argument("--wait-seconds", type=int, default=30)
    p_apply.add_argument("--config-paths", default="",
                         help=f"OS-pathsep ({os.pathsep!r}) joined list of config files to back up")

    p_roll = sub.add_parser("rollback", help="restore a previous backup")
    p_roll.add_argument("--backup", required=True)
    p_roll.add_argument("--restart-argv", default="")
    p_roll.add_argument("--restart-cwd", default="")
    p_roll.add_argument("--restart-log", default="")
    p_roll.add_argument("--restart-pidfile", default="")
    p_roll.add_argument("--health-url", default="http://127.0.0.1:8888/api/dashboard/today")
    p_roll.add_argument("--wait-seconds", type=int, default=30)

    sub.add_parser("list-backups", help="JSON list of snapshots")

    p_prune = sub.add_parser("prune", help="delete backups older than --keep")
    p_prune.add_argument("--keep", type=int, default=backup_mod.KEEP_RECENT)

    args = parser.parse_args(argv)

    if args.cmd == "check":
        result = version_check.check(force=args.force)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "apply":
        status = apply_update(
            target_version=args.target_version,
            wheel_url=args.wheel_url,
            restart=_restart_spec_from_args(args),
            config_paths=_config_paths(args),
            server_pid=args.server_pid,
        )
        return 0 if status.phase == "completed" else 1

    if args.cmd == "rollback":
        status = rollback(args.backup, restart=_restart_spec_from_args(args))
        return 0 if status.phase == "completed" else 1

    if args.cmd == "list-backups":
        out = [b.to_dict() for b in backup_mod.list_backups()]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "prune":
        removed = backup_mod.prune_backups(keep_recent=args.keep)
        print(json.dumps({"removed": removed}, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
