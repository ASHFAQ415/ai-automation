import pytest
from sqlalchemy import text
from app.database import AsyncSessionLocal, init_db

@pytest.mark.asyncio
async def test_sqlite_wal_mode_active():
    await init_db()
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("PRAGMA journal_mode;"))
        mode = result.scalar()
        assert str(mode).lower() == "wal", f"Expected WAL mode, got {mode}"

@pytest.mark.asyncio
async def test_foreign_keys_enabled():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("PRAGMA foreign_keys;"))
        fk = result.scalar()
        assert fk == 1, f"Expected foreign keys = 1, got {fk}"
