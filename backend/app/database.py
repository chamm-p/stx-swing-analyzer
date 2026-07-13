"""Async-Engine, Session-Factory und Schema-Bootstrap (TimescaleDB).

Greenfield-Ansatz: SQLAlchemy ``create_all`` + idempotente Hypertable-/
Retention-Statements beim Start. Bei späteren Schema-Änderungen auf
Alembic umstellen.
"""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Erstellt Tabellen, Hypertables und Retention-Policies (idempotent)."""
    from app import models  # noqa: F401 — Modelle registrieren

    async with engine.begin() as conn:
        # Backend + Worker starten parallel und rufen beide init_db() —
        # der Advisory-Lock serialisiert das DDL.
        await conn.execute(text("SELECT pg_advisory_xact_lock(731400)"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
        await conn.run_sync(models.Base.metadata.create_all)

        await conn.execute(text(
            "SELECT create_hypertable('ohlcv', 'ts', if_not_exists => TRUE, migrate_data => TRUE)"
        ))
        await conn.execute(text(
            "SELECT create_hypertable('news_articles', 'published_at', if_not_exists => TRUE, migrate_data => TRUE)"
        ))
        await conn.execute(text(
            f"SELECT add_retention_policy('ohlcv', INTERVAL '{settings.retention_ohlcv_days} days', if_not_exists => TRUE)"
        ))
        await conn.execute(text(
            f"SELECT add_retention_policy('news_articles', INTERVAL '{settings.retention_news_days} days', if_not_exists => TRUE)"
        ))
    logger.info("DB-Schema initialisiert (Hypertables + Retention-Policies aktiv)")
