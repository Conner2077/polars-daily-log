import json
import subprocess
from datetime import date, datetime
from typing import Optional

from ..models.database import Database


class GitCollector:
    def __init__(self, db: Database):
        self._db = db

    async def collect_today(self, target_date: Optional[date] = None) -> int:
        target = target_date or date.today()
        date_str = target.isoformat()
        after = f"{date_str} 00:00:00"
        before = f"{date_str} 23:59:59"

        repos = await self._db.fetch_all(
            "SELECT * FROM git_repos WHERE is_active = 1"
        )
        total = 0
        for repo in repos:
            count = await self._collect_repo(repo, after, before, date_str)
            total += count
        return total

    async def _collect_repo(
        self, repo: dict, after: str, before: str, date_str: str
    ) -> int:
        path = repo["path"]
        email = repo["author_email"]
        repo_id = repo["id"]

        fmt = "%H|||%s|||%ae|||%aI|||"
        cmd = [
            "git", "-C", path, "log",
            f"--after={after}", f"--before={before}",
            f"--format={fmt}",
        ]
        if email:
            cmd.append(f"--author={email}")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return 0

        if result.returncode != 0 or not result.stdout.strip():
            return 0

        count = 0
        for line in result.stdout.strip().split("\n"):
            if "|||" not in line:
                continue
            parts = line.split("|||")
            if len(parts) < 4:
                continue
            commit_hash, message, author, committed_at = parts[0], parts[1], parts[2], parts[3]

            existing = await self._db.fetch_one(
                "SELECT id FROM git_commits WHERE hash = ? AND repo_id = ?",
                (commit_hash, repo_id),
            )
            if existing:
                continue

            stat_cmd = [
                "git", "-C", path, "diff-tree", "--no-commit-id",
                "--numstat", "-r", "--root", commit_hash,
            ]
            stat_result = subprocess.run(
                stat_cmd, capture_output=True, text=True, timeout=10
            )
            files = []
            insertions = 0
            deletions = 0
            if stat_result.returncode == 0:
                for stat_line in stat_result.stdout.strip().split("\n"):
                    if not stat_line:
                        continue
                    stat_parts = stat_line.split("\t")
                    if len(stat_parts) >= 3:
                        ins = int(stat_parts[0]) if stat_parts[0] != "-" else 0
                        dels = int(stat_parts[1]) if stat_parts[1] != "-" else 0
                        insertions += ins
                        deletions += dels
                        files.append(stat_parts[2])

            await self._db.execute(
                """INSERT INTO git_commits
                   (repo_id, hash, message, author, committed_at,
                    files_changed, insertions, deletions, date, machine_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    repo_id, commit_hash, message, author, committed_at,
                    json.dumps(files), insertions, deletions, date_str, "local",
                ),
            )
            count += 1
        return count
