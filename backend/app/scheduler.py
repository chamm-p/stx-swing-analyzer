"""APScheduler-Jobs: Marktdaten-Sync, News-Fetch, Analyse-Pipeline.

Läuft im Worker-Container (worker_main.py). max_instances=1 verhindert
überlappende Läufe bei langsamen LLM-Antworten.
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.analysis.auto_trader import run_auto_portfolios
from app.analysis.pipeline import run_all
from app.analysis.screener import scan_universe
from app.analysis.signal_review import evaluate_signals
from app.config import get_settings
from app.database import SessionLocal
from app.models import Position, WatchlistItem
from app.sources import rss, yahoo

logger = logging.getLogger(__name__)


async def job_sync_market() -> None:
    """Kurs-Sync für Watchlist + offene Portfolio-Positionen.

    Wiederholte Fehler pro Symbol lösen einen Ops-Alarm aus (Telegram/
    E-Mail) — sonst fiele ein stiller Ausfall der Kursdaten erst auf,
    wenn Signale auf veralteten Kursen basieren."""
    from app.alerts.ops import track_failure, track_success

    async with SessionLocal() as db:
        wl = await db.execute(select(WatchlistItem.symbol))
        pos = await db.execute(
            select(Position.symbol).where(Position.exit_date.is_(None)).distinct()
        )
        symbols = {row[0] for row in wl.all()} | {row[0] for row in pos.all()}
        for symbol in sorted(symbols):
            try:
                await yahoo.sync_ohlcv(db, symbol)
                await track_success(f"market:{symbol}")
            except Exception as e:
                logger.error("Markt-Sync %s fehlgeschlagen: %s", symbol, e)
                await track_failure(db, f"market:{symbol}", str(e),
                                    subject=f"Kurs-Sync für {symbol}")


async def job_sync_news() -> None:
    from app.alerts.ops import track_failure, track_success
    from app.models import DataSource

    async with SessionLocal() as db:
        try:
            await rss.fetch_all_sources(db)
        except Exception as e:
            logger.error("News-Sync fehlgeschlagen: %s", e)
        try:
            await rss.fetch_symbol_news(db)
        except Exception as e:
            logger.error("Symbol-News-Sync fehlgeschlagen: %s", e)

        # Quellen-Gesundheit: wiederholt fehlschlagende Feeds melden
        result = await db.execute(select(DataSource).where(DataSource.enabled == True))  # noqa: E712
        for source in result.scalars().all():
            if source.last_error:
                await track_failure(db, f"news:{source.id}", source.last_error,
                                    subject=f"News-Quelle „{source.name}“")
            else:
                await track_success(f"news:{source.id}")


async def job_scan_universe() -> None:
    async with SessionLocal() as db:
        try:
            await scan_universe(db)
        except Exception as e:
            logger.exception("Universum-Scan fehlgeschlagen: %s", e)


async def job_analyze() -> None:
    async with SessionLocal() as db:
        count = await run_all(db)
        logger.info("Analyse-Lauf abgeschlossen: %d neue Signale", count)
    # Direkt danach: Auto-Portfolios handeln auf frischen Signalen
    await job_paper_trading()


async def job_paper_trading() -> None:
    """Signal-Review + Auto-Portfolio-Trading (Paper)."""
    async with SessionLocal() as db:
        try:
            await evaluate_signals(db)
        except Exception as e:
            logger.exception("Signal-Review fehlgeschlagen: %s", e)
        try:
            stats = await run_auto_portfolios(db)
            if stats["opened"] or stats["closed"]:
                logger.info("Auto-Trading: %d Käufe, %d Verkäufe",
                            stats["opened"], stats["closed"])
        except Exception as e:
            logger.exception("Auto-Trading fehlgeschlagen: %s", e)


def build_scheduler() -> AsyncIOScheduler:
    s = get_settings()
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(job_sync_market, "interval", minutes=s.fetch_market_interval_min,
                      id="sync_market", max_instances=1, coalesce=True)
    scheduler.add_job(job_sync_news, "interval", minutes=s.fetch_news_interval_min,
                      id="sync_news", max_instances=1, coalesce=True)
    scheduler.add_job(job_analyze, "interval", minutes=s.analyze_interval_min,
                      id="analyze", max_instances=1, coalesce=True)
    scheduler.add_job(job_scan_universe, "interval", minutes=s.scan_interval_min,
                      id="scan_universe", max_instances=1, coalesce=True)
    # Stündlich zusätzlich: Horizont-Exits + fällige Signal-Auswertungen,
    # unabhängig vom Analyse-Rhythmus
    scheduler.add_job(job_paper_trading, "interval", minutes=60,
                      id="paper_trading", max_instances=1, coalesce=True)
    return scheduler
