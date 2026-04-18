"""Publisher registry — resolve the right publisher for a summary type.

Call ``get_publisher(db, summary_type_name)`` → ``WorklogPublisher | None``.
Returns None when the summary type has no publisher configured (e.g. weekly
reports that don't get pushed anywhere).
"""
from __future__ import annotations

import json
from typing import Optional

from ..models.database import Database
from . import WorklogPublisher


async def get_publisher_for_output(db: Database, output_id: int) -> Optional[WorklogPublisher]:
    """Build and return the publisher configured for a scope_output row."""
    row = await db.fetch_one(
        "SELECT publisher_name, publisher_config FROM scope_outputs WHERE id = ?",
        (output_id,),
    )
    if not row or not row.get("publisher_name"):
        return None

    name = row["publisher_name"]
    factory = _FACTORIES.get(name)
    if factory is None:
        return None

    config = {}
    try:
        config = json.loads(row.get("publisher_config") or "{}")
    except (json.JSONDecodeError, TypeError):
        pass

    return await factory(db, config)


async def get_publisher(db: Database, summary_type_name: str) -> Optional[WorklogPublisher]:
    """Build and return the publisher configured for ``summary_type_name``.

    Each call creates a fresh publisher instance (cheap) because the
    underlying credentials may change between calls (user re-logins).
    """
    row = await db.fetch_one(
        "SELECT publisher_name, publisher_config FROM summary_types WHERE name = ?",
        (summary_type_name,),
    )
    if not row or not row.get("publisher_name"):
        return None

    name = row["publisher_name"]
    factory = _FACTORIES.get(name)
    if factory is None:
        return None

    config = {}
    try:
        config = json.loads(row.get("publisher_config") or "{}")
    except (json.JSONDecodeError, TypeError):
        pass

    return await factory(db, config)


async def _build_jira(db: Database, config: dict) -> WorklogPublisher:
    from ..jira_client.client import build_jira_client_from_db
    from .jira import JiraPublisher
    client = await build_jira_client_from_db(db)
    return JiraPublisher(client)


async def _build_webhook(db: Database, config: dict) -> WorklogPublisher:
    from .webhook import WebhookPublisher
    return WebhookPublisher(config)


# ── Factory map — add new publishers here ──────────────────────────────
# Each factory receives (db, publisher_config_dict) and returns a publisher.
_FACTORIES: dict = {
    "jira": _build_jira,
    "webhook": _build_webhook,
}
