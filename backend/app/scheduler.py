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


async def job_auto_optimize() -> None:
    """Quartals-Optimierung: Walk-Forward mit System-Grid je Segment,
    Ergebnis + Empfehlung über die Alert-Kanäle. Approve (Challenger)
    bleibt manuell auf der Backtest-Seite."""
    import asyncio

    from app.alerts.dispatcher import send_email_sync, send_telegram
    from app.backtest.runner import recommendation_from, start_run
    from app.models import BacktestRun, TradingPlatform
    from app.services_settings import load_settings

    s = get_settings()
    days = min(3650, s.retention_ohlcv_days)
    async with SessionLocal() as db:
        platform = await db.scalar(select(TradingPlatform).limit(1))
        platform_id = platform.id if platform else None

    for segment in [x.strip().upper() for x in s.optimize_segments.split(",") if x.strip()]:
        try:
            run_id = await start_run({
                "label": f"Quartals-Optimierung {segment}",
                "segment": segment, "days": days, "backfill": True,
                "platform_id": platform_id, "mode": "optimize",
                "train_days": 365, "test_days": 90,
                "min_trades": 20, "min_train_score": 0.0, "params": {},
            }, background=False)
        except Exception as e:
            logger.exception("Quartals-Optimierung %s fehlgeschlagen: %s", segment, e)
            continue

        async with SessionLocal() as db:
            run = await db.get(BacktestRun, run_id)
            m = run.metrics or {}
            reco = recommendation_from(m)
            if run.status != "done":
                text = f"⚠️ Quartals-Optimierung {segment} fehlgeschlagen: {run.error}"
            else:
                lines = [
                    f"📊 Quartals-Optimierung {segment} abgeschlossen",
                    f"OOS: {m.get('total_return_pct')}% vs {m.get('benchmark_symbol') or 'Benchmark'} "
                    f"{m.get('benchmark_return_pct')}% | Sharpe {m.get('sharpe')}",
                    f"Fenster: {m.get('windows_tested')} gehandelt, {m.get('windows_flat')} flat "
                    f"| Stabilität {m.get('param_stability')}",
                ]
                if reco:
                    params_text = ", ".join(f"{k}={v}" for k, v in reco["params"].items())
                    lines.append(f"🏆 Empfehlung: {params_text} "
                                 f"(gewählt in {reco['wins']}/{reco['windows_tested']} Fenstern)")
                lines.append("→ Approve als Challenger: Backtest-Seite")
                text = "\n".join(lines)
            comm = await load_settings(db, "comm")
        if comm.get("telegram_bot_token") and comm.get("telegram_chat_id"):
            try:
                await send_telegram(comm, text)
            except Exception as e:
                logger.error("Optimierungs-Telegram fehlgeschlagen: %s", e)
        if comm.get("smtp_host") and comm.get("alert_email_to"):
            try:
                await asyncio.to_thread(send_email_sync, comm,
                                        f"[stx] Quartals-Optimierung {segment}", text)
            except Exception as e:
                logger.error("Optimierungs-E-Mail fehlgeschlagen: %s", e)


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
    if s.optimize_interval_days > 0:
        scheduler.add_job(job_auto_optimize, "interval",
                          days=s.optimize_interval_days,
                          id="auto_optimize", max_instances=1, coalesce=True)
    return scheduler
