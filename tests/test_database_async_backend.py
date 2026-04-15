import asyncio
import sys
import types


class _StubConnection:
    pass


sys.modules.setdefault("sqlite_vec", types.SimpleNamespace(loadable_path=lambda: "/tmp/sqlite_vec.so"))
sys.modules.setdefault("aiosqlite", types.SimpleNamespace(connect=None, Row="FAKE_ROW", Connection=_StubConnection))

from auto_daily_log.models import database as database_module


def test_initialize_uses_aiosqlite_connect(monkeypatch, tmp_path):
    calls = []

    class FakeConnection:
        def __init__(self):
            self.row_factory = None

        async def enable_load_extension(self, enabled):
            calls.append(("enable_load_extension", enabled))

        async def load_extension(self, path):
            calls.append(("load_extension", path))

        async def executescript(self, script):
            calls.append(("executescript", script.startswith("\nCREATE TABLE IF NOT EXISTS activities")))

        async def execute(self, sql, params=()):
            calls.append(("execute", sql, params))

            class FakeCursor:
                lastrowid = 1

            return FakeCursor()

        async def commit(self):
            calls.append(("commit", True))

        async def close(self):
            calls.append(("close", True))

    async def fake_connect(path):
        calls.append(("connect", str(path)))
        return FakeConnection()

    async def fake_migrate(self):
        calls.append(("migrate", True))

    monkeypatch.setattr(
        database_module,
        "aiosqlite",
        types.SimpleNamespace(connect=fake_connect, Row="FAKE_ROW", Connection=FakeConnection),
    )
    monkeypatch.setattr(database_module.sqlite_vec, "loadable_path", lambda: "/tmp/sqlite_vec.so")
    monkeypatch.setattr(database_module.Database, "_migrate", fake_migrate)

    database = database_module.Database(tmp_path / "test.db", embedding_dimensions=4)
    asyncio.run(database.initialize())

    assert calls[0] == ("connect", str(tmp_path / "test.db"))
    assert calls[1] == ("enable_load_extension", True)
    assert calls[2] == ("load_extension", "/tmp/sqlite_vec.so")
    assert database._conn.row_factory == "FAKE_ROW"
