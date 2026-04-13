import json
from ..models.database import Database
from .embedding import EmbeddingEngine


class Indexer:
    def __init__(self, db: Database, engine: EmbeddingEngine):
        self._db = db
        self._engine = engine

    async def index_worklogs(self, target_date: str = None) -> int:
        """Index worklog drafts (daily/weekly/monthly summaries) — the most searchable content."""
        if target_date:
            drafts = await self._db.fetch_all(
                "SELECT * FROM worklog_drafts WHERE date = ? AND summary IS NOT NULL AND summary != ''",
                (target_date,),
            )
        else:
            drafts = await self._db.fetch_all(
                "SELECT * FROM worklog_drafts WHERE summary IS NOT NULL AND summary != ''"
            )
        count = 0
        for d in drafts:
            existing = await self._db.fetch_one(
                "SELECT rowid FROM embeddings WHERE source_type = 'worklog' AND source_id = ?",
                (d["id"],),
            )
            if existing:
                continue
            tag = d.get("tag", "daily")
            period = f"{d.get('period_start', d['date'])} ~ {d.get('period_end', d['date'])}"
            text = f"[{tag}] {d.get('issue_key', '')} ({period})\n{d['summary']}"
            vec = await self._engine.embed(text)
            await self._db.execute(
                "INSERT INTO embeddings (source_type, source_id, text_content, embedding) "
                "VALUES (?, ?, ?, ?)",
                ("worklog", d["id"], text, json.dumps(vec)),
            )
            count += 1
        return count

    async def index_commits(self, target_date: str = None) -> int:
        """Index git commits — clear semantic content."""
        if target_date:
            commits = await self._db.fetch_all(
                "SELECT * FROM git_commits WHERE date = ?", (target_date,)
            )
        else:
            commits = await self._db.fetch_all("SELECT * FROM git_commits")
        count = 0
        for c in commits:
            existing = await self._db.fetch_one(
                "SELECT rowid FROM embeddings WHERE source_type = 'git_commit' AND source_id = ?",
                (c["id"],),
            )
            if existing:
                continue
            text = f"{c['message']} ({c.get('files_changed', '')})"
            vec = await self._engine.embed(text)
            await self._db.execute(
                "INSERT INTO embeddings (source_type, source_id, text_content, embedding) "
                "VALUES (?, ?, ?, ?)",
                ("git_commit", c["id"], text, json.dumps(vec)),
            )
            count += 1
        return count

    async def reindex_all(self) -> dict:
        """Rebuild entire search index from worklogs + commits."""
        # Clear existing
        await self._db.execute("DELETE FROM embeddings WHERE source_type IN ('worklog', 'git_commit')")
        wl = await self.index_worklogs()
        gc = await self.index_commits()
        return {"worklogs": wl, "git_commits": gc}
