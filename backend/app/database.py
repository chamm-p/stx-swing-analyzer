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
        # Retention-Policies: remove+add statt if_not_exists, damit
        # Änderungen an RETENTION_* in der .env tatsächlich greifen
        # (add_retention_policy aktualisiert bestehende Policies nicht).
        for table, days in (("ohlcv", settings.retention_ohlcv_days),
                            ("news_articles", settings.retention_news_days)):
            await conn.execute(text(
                f"SELECT remove_retention_policy('{table}', if_exists => TRUE)"
            ))
            await conn.execute(text(
                f"SELECT add_retention_policy('{table}', INTERVAL '{days} days', if_not_exists => TRUE)"
            ))

        # Leichtgewichtige Spalten-Migrationen: create_all ergänzt keine
        # Spalten an bestehenden Tabellen. Erst prüfen, dann ALTER — ein
        # ALTER braucht ACCESS EXCLUSIVE und würde bei jedem Start hinter
        # laufenden Worker-Transaktionen hängen bleiben.
        migrations = (
            ("portfolios", "cash", "DOUBLE PRECISION NOT NULL DEFAULT 0"),
            ("portfolios", "config", "JSONB"),
            ("portfolios", "watch_enabled", "BOOLEAN NOT NULL DEFAULT true"),
            ("positions", "source", "VARCHAR(10) NOT NULL DEFAULT 'manual'"),
            ("positions", "signal_id", "UUID"),
            ("positions", "horizon_days", "INTEGER"),
            ("positions", "target_price", "DOUBLE PRECISION"),
            ("positions", "stop_price", "DOUBLE PRECISION"),
            ("signals", "target_price", "DOUBLE PRECISION"),
            ("signals", "stop_price", "DOUBLE PRECISION"),
            ("signals", "risk_reward", "DOUBLE PRECISION"),
            ("signals", "analyst_target", "DOUBLE PRECISION"),
            ("signals", "analyst_count", "INTEGER"),
            ("signals", "eval_target_hit", "BOOLEAN"),
            ("news_articles", "sentiment_relevance", "DOUBLE PRECISION"),
            ("signals", "eval_price", "DOUBLE PRECISION"),
            ("signals", "eval_return_pct", "DOUBLE PRECISION"),
            ("signals", "eval_hit", "BOOLEAN"),
            ("signals", "evaluated_at", "TIMESTAMPTZ"),
        )
        existing = {
            (row[0], row[1]) for row in (await conn.execute(text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public'"
            ))).all()
        }
        for table, column, ddl_type in migrations:
            if (table, column) not in existing:
                await conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl_type}"
                ))
    logger.info("DB-Schema initialisiert (Hypertables + Retention-Policies aktiv)")
