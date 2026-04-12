import pytest
import pytest_asyncio
from auto_daily_log.models.database import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db", embedding_dimensions=4)
    await database.initialize()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_embeddings_table_exists(db):
    tables = await db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    table_names = [t["name"] for t in tables]
    assert "embeddings" in table_names


@pytest.mark.asyncio
async def test_insert_and_search_embedding(db):
    await db.execute(
        "INSERT INTO embeddings (source_type, source_id, text_content, embedding) VALUES (?, ?, ?, ?)",
        ("activity", 1, "coding in IntelliJ", "[1.0, 0.0, 0.0, 0.0]"),
    )
    await db.execute(
        "INSERT INTO embeddings (source_type, source_id, text_content, embedding) VALUES (?, ?, ?, ?)",
        ("activity", 2, "meeting in Zoom", "[0.0, 1.0, 0.0, 0.0]"),
    )

    rows = await db.fetch_all(
        "SELECT source_type, source_id, text_content, distance "
        "FROM embeddings WHERE embedding MATCH ? ORDER BY distance LIMIT 2",
        ("[1.0, 0.1, 0.0, 0.0]",),
    )
    assert len(rows) == 2
    assert rows[0]["source_id"] == 1
    assert rows[0]["text_content"] == "coding in IntelliJ"
