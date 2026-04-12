import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from auto_daily_log.web.app import create_app
from auto_daily_log.models.database import Database

@pytest_asyncio.fixture
async def app_client(tmp_path):
    db = Database(tmp_path / "test.db", embedding_dimensions=4)
    await db.initialize()
    app = create_app(db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await db.close()
