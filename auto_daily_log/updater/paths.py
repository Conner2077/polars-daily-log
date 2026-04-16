"""Filesystem layout for updater state and backups.

All paths live under the same data_dir the rest of the app uses, so a
user who customised system.data_dir gets backups in the right place too.
"""
from __future__ import annotations

from pathlib import Path

from ..config import load_config
import os


def data_dir() -> Path:
    cfg = load_config(os.environ.get("PDL_SERVER_CONFIG"))
    return cfg.system.resolved_data_dir


def state_dir() -> Path:
    p = data_dir() / "state"
    p.mkdir(parents=True, exist_ok=True)
    return p


def backups_dir() -> Path:
    p = data_dir() / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


def update_check_path() -> Path:
    return state_dir() / "update_check.json"


def update_status_path() -> Path:
    return state_dir() / "update_status.json"
