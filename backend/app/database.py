"""Database configuration and async session management."""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.config import settings

# aiosqlite does NOT accept check_same_thread — removed to prevent silent warnings
_connect_args = {}

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args=_connect_args,
)

# Enforce WAL mode, foreign keys, and performant synchronous mode on all connections
@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

Base = declarative_base()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for obtaining an async database session.
    Commits on clean exit, rolls back on any exception.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Create all tables and ensure SQLite WAL mode and column migrations are active."""
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA synchronous=NORMAL"))
        await conn.execute(text("PRAGMA foreign_keys=ON"))
        await conn.run_sync(Base.metadata.create_all)

        # Safe column migrations for existing SQLite databases
        cols_result = await conn.execute(text("PRAGMA table_info(leads)"))
        existing_cols = {row[1] for row in cols_result.fetchall()}

        if "payment_status" not in existing_cols:
            await conn.execute(text("ALTER TABLE leads ADD COLUMN payment_status VARCHAR(50) DEFAULT 'UNPAID'"))
        if "payment_link" not in existing_cols:
            await conn.execute(text("ALTER TABLE leads ADD COLUMN payment_link VARCHAR(500)"))
        if "advance_amount" not in existing_cols:
            await conn.execute(text("ALTER TABLE leads ADD COLUMN advance_amount INTEGER DEFAULT 0"))
        if "contact_status" not in existing_cols:
            await conn.execute(text("ALTER TABLE leads ADD COLUMN contact_status VARCHAR(50) DEFAULT 'OPTED_IN'"))
        if "tenant_id" not in existing_cols:
            await conn.execute(text("ALTER TABLE leads ADD COLUMN tenant_id VARCHAR(50) DEFAULT 'default_tenant'"))
        if "quote_number" not in existing_cols:
            await conn.execute(text("ALTER TABLE leads ADD COLUMN quote_number VARCHAR(50)"))
        if "invoice_number" not in existing_cols:
            await conn.execute(text("ALTER TABLE leads ADD COLUMN invoice_number VARCHAR(50)"))

        build_cols_result = await conn.execute(text("PRAGMA table_info(project_builds)"))
        existing_build_cols = {row[1] for row in build_cols_result.fetchall()}

        if "tenant_id" not in existing_build_cols:
            await conn.execute(text("ALTER TABLE project_builds ADD COLUMN tenant_id VARCHAR(50) DEFAULT 'default_tenant'"))
        if "version" not in existing_build_cols:
            await conn.execute(text("ALTER TABLE project_builds ADD COLUMN version INTEGER DEFAULT 1"))
        if "artifact_checksum" not in existing_build_cols:
            await conn.execute(text("ALTER TABLE project_builds ADD COLUMN artifact_checksum VARCHAR(64)"))
        if "verification_report" not in existing_build_cols:
            await conn.execute(text("ALTER TABLE project_builds ADD COLUMN verification_report TEXT"))
        if "gate_passed" not in existing_build_cols:
            await conn.execute(text("ALTER TABLE project_builds ADD COLUMN gate_passed BOOLEAN DEFAULT 0"))

        plan_cols_result = await conn.execute(text("PRAGMA table_info(project_goal_plans)"))
        existing_plan_cols = {row[1] for row in plan_cols_result.fetchall()}

        if "tenant_id" not in existing_plan_cols:
            await conn.execute(text("ALTER TABLE project_goal_plans ADD COLUMN tenant_id VARCHAR(50) DEFAULT 'default_tenant'"))
        if "risk_level" not in existing_plan_cols:
            await conn.execute(text("ALTER TABLE project_goal_plans ADD COLUMN risk_level VARCHAR(20) DEFAULT 'LOW'"))
        if "risk_signals" not in existing_plan_cols:
            await conn.execute(text("ALTER TABLE project_goal_plans ADD COLUMN risk_signals TEXT"))
        if "human_acceptance_required" not in existing_plan_cols:
            await conn.execute(text("ALTER TABLE project_goal_plans ADD COLUMN human_acceptance_required BOOLEAN DEFAULT 0"))
        if "human_accepted" not in existing_plan_cols:
            await conn.execute(text("ALTER TABLE project_goal_plans ADD COLUMN human_accepted BOOLEAN DEFAULT 0"))
        if "human_reviewer_id" not in existing_plan_cols:
            await conn.execute(text("ALTER TABLE project_goal_plans ADD COLUMN human_reviewer_id VARCHAR(100)"))
        if "human_accepted_at" not in existing_plan_cols:
            await conn.execute(text("ALTER TABLE project_goal_plans ADD COLUMN human_accepted_at DATETIME"))
        if "human_review_notes" not in existing_plan_cols:
            await conn.execute(text("ALTER TABLE project_goal_plans ADD COLUMN human_review_notes TEXT"))
