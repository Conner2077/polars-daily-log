"""Polars Daily Log - Automated Jira worklog tool."""
from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("auto-daily-log")
except PackageNotFoundError:
    # Editable install before metadata is generated, or running from a checkout
    # without `pip install -e .` — fall back to a sentinel that's obviously not
    # a real release version.
    __version__ = "0.0.0+local"
