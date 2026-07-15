"""APScheduler-Jobs: Marktdaten-Sync, News-Fetch, Analyse-Pipeline.

Läuft im Worker-Container (worker_main.py). max_instances=1 verhindert
überlappende Läufe bei langsamen LLM-Antworten. Ein 20s-Tick nimmt
Ad-hoc-Trigger aus der API auf und gleicht Intervalle mit den
Runtime-Settings (Kategorie "scheduler") ab — Änderungen im UI greifen
ohne Worker-Neustart.
"""

import asyncio
import logging
import time

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
        sched_cfg = await load_settings(db, "scheduler")
    segments_cfg = str(sched_cfg.get("optimize_segments") or s.optimize_segments)

    for segment in [x.strip().upper() for x in segments_cfg.split(",") if x.strip()]:
        # Eskalationsleiter: erst normaler Flat-Guard, bei negativem OOS ein
        # zweiter Lauf mit strengem Guard (handelt nur Fenster mit deutlich
        # positivem Training). Bleibt es negativ, wird KEIN Parametersatz
        # empfohlen — weiter zu suchen wäre Overfitting.
        run = None
        guard_used = 0.0
        try:
            for guard in (0.0, 0.5):
                label = f"Quartals-Optimierung {segment}"
                if guard > 0:
                    label += f" (streng, Flat-Guard {guard})"
                run_id = await start_run({
                    "label": label,
                    "segment": segment, "days": days, "backfill": True,
                    "platform_id": platform_id, "mode": "optimize",
                    "train_days": 365, "test_days": 90,
                    "min_trades": 20, "min_train_score": guard, "params": {},
                }, background=False)
                async with SessionLocal() as db:
                    run = await db.get(BacktestRun, run_id)
                    db.expunge(run)
                guard_used = guard
                ret = (run.metrics or {}).get("total_return_pct")
                if run.status != "done" or (ret is not None and ret > 0):
                    break
        except Exception as e:
            logger.exception("Quartals-Optimierung %s fehlgeschlagen: %s", segment, e)
            continue

        m = run.metrics or {}
        reco = recommendation_from(m)
        detail_lines: list[str] = []
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
            if reco and reco.get("verdict") == "no_trade":
                lines.append(f"🛑 {reco['reason']}")
                lines.append("(Auch der strenge zweite Lauf blieb negativ — "
                             "kein Parametersatz wird empfohlen.)")
            elif reco:
                params_text = ", ".join(f"{k}={v}" for k, v in reco["params"].items())
                if guard_used > 0:
                    params_text += f", min_train_score={guard_used}"
                lines.append(f"🏆 Empfehlung: {params_text} "
                             f"(gewählt in {reco['wins']}/{reco['windows_tested']} Fenstern)")
                if guard_used > 0:
                    lines.append("⚠️ Nur mit strengem Flat-Guard profitabel — "
                                 "Strategie handelt dann selten.")
                lines.append("→ Approve als Challenger: Backtest-Seite")
            text = "\n".join(lines)

            # Detailbericht (nur E-Mail — Telegram bleibt kompakt):
            # Fenstertabelle, Parameter-Streuung, Vergleich zum Champion
            from app.analysis.scoring import load_champion
            async with SessionLocal() as db:
                current = await load_champion(db)
            detail_lines.append("— Detailbericht —")
            detail_lines.append(f"Aktive Live-Strategie: "
                                f"{current or 'Defaults (threshold 0.35, Ziel 2.0×, Stop 1.5×)'}")
            wins = m.get("param_wins") or {}
            if wins:
                detail_lines.append("Parameter-Gewinner über die Fenster:")
                for key, count in sorted(wins.items(), key=lambda kv: -kv[1])[:5]:
                    detail_lines.append(f"  {count}× {key}")
            detail_lines.append("")
            detail_lines.append("Fenster (Test-Zeitraum | Parameter | OOS-Rendite | Trades):")
            for w in (m.get("windows") or []):
                test = "→".join(w.get("test", []))
                if w.get("skipped"):
                    detail_lines.append(f"  {test} | ÜBERSPRUNGEN: {w['skipped']}")
                elif w.get("flat"):
                    detail_lines.append(f"  {test} | 💤 FLAT ({w['flat']})")
                else:
                    p = w.get("chosen_params") or {}
                    params_short = ", ".join(f"{k.split('_')[0]}={v}" for k, v in p.items())
                    detail_lines.append(
                        f"  {test} | {params_short} | {w.get('test_return_pct')}% "
                        f"| {w.get('test_trades')} Trades")
        async with SessionLocal() as db:
            comm = await load_settings(db, "comm")
        if comm.get("telegram_bot_token") and comm.get("telegram_chat_id"):
            try:
                await send_telegram(comm, text)
            except Exception as e:
                logger.error("Optimierungs-Telegram fehlgeschlagen: %s", e)
        if comm.get("smtp_host") and comm.get("alert_email_to"):
            try:
                body = text + ("\n\n" + "\n".join(detail_lines) if detail_lines else "")
                await asyncio.to_thread(send_email_sync, comm,
                                        f"[stx] Quartals-Optimierung {segment}", body)
            except Exception as e:
                logger.error("Optimierungs-E-Mail fehlgeschlagen: %s", e)


async def job_digest() -> None:
    """Tägliche Handelsempfehlung: Kauf-Kandidaten (1%-Regel-Stückzahlen)
    + Halten/Verkaufen-Review je offener Position, via Alert-Kanäle."""
    from app.analysis.recommendations import send_digest

    async with SessionLocal() as db:
        info = await send_digest(db)
        logger.info("Digest: %s", info)


async def job_discovery() -> None:
    """Nächtlicher Discovery-Scan über die kompletten Börsenverzeichnisse."""
    from app.alerts.ops import track_failure, track_success
    from app.analysis.discovery import run_discovery

    async with SessionLocal() as db:
        try:
            count = await run_discovery(db)
            await track_success("discovery")
            logger.info("Discovery-Job fertig: %d Kandidaten", count)
        except Exception as e:
            logger.exception("Discovery-Scan fehlgeschlagen: %s", e)
            await track_failure(db, "discovery", str(e), subject="Discovery-Scan")


async def job_refresh_universe() -> None:
    """Index-Mitgliedschaften (S&P 500, Nasdaq 100, DAX/MDAX/SDAX, Euro
    Stoxx 50) aktuell halten — Indizes rotieren Mitglieder regelmäßig."""
    from app.sources.indices import refresh_indices

    async with SessionLocal() as db:
        try:
            result = await refresh_indices(db)
            logger.info("Universum-Refresh: %s", result)
        except Exception as e:
            logger.exception("Universum-Refresh fehlgeschlagen: %s", e)


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


# -------------------------------------------------------- Job-Verwaltung

JOB_FUNCS = {
    "sync_market": job_sync_market,
    "sync_news": job_sync_news,
    "analyze": job_analyze,
    "scan_universe": job_scan_universe,
    "paper_trading": job_paper_trading,
    "discovery": job_discovery,
    "digest": job_digest,
    "auto_optimize": job_auto_optimize,
    "refresh_universe": job_refresh_universe,
}

_scheduler: AsyncIOScheduler | None = None
_applied: dict[str, tuple] = {}  # job_id -> zuletzt angewendetes Intervall


def wrapped_job(job_id: str):
    """Job mit Lauf-Protokoll (Redis) und Doppelstart-Schutz."""
    fn = JOB_FUNCS[job_id]

    async def run() -> None:
        from app import jobs
        if not await jobs.acquire_lock(job_id):
            logger.info("Job %s übersprungen — läuft bereits", job_id)
            return
        t0 = time.monotonic()
        try:
            await fn()
            await jobs.record_run(job_id, True, duration_s=time.monotonic() - t0)
        except Exception as e:
            logger.exception("Job %s fehlgeschlagen: %s", job_id, e)
            await jobs.record_run(job_id, False, str(e), duration_s=time.monotonic() - t0)
        finally:
            await jobs.release_lock(job_id)

    run.__name__ = f"job_{job_id}"
    return run


def _parse_time(raw) -> tuple[int, int] | None:
    try:
        hour, minute = str(raw).strip().split(":")
        hour, minute = int(hour), int(minute)
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except (ValueError, AttributeError):
        pass
    return None


def _parse_times(raw) -> list[tuple[int, int]] | None:
    """Komma-Liste "16:45,21:15" → [(16,45), (21,15)]; None wenn unbrauchbar."""
    times = [_parse_time(t) for t in str(raw or "").split(",") if t.strip()]
    if not times or any(t is None for t in times):
        return None
    return sorted(set(times))  # type: ignore[arg-type]


def _times_trigger(times: list[tuple[int, int]]):
    from apscheduler.triggers.combining import OrTrigger
    from apscheduler.triggers.cron import CronTrigger

    crons = [CronTrigger(hour=h, minute=m, timezone="UTC") for h, m in times]
    return crons[0] if len(crons) == 1 else OrTrigger(crons)


async def job_tick() -> None:
    """Ad-hoc-Trigger ausführen, Intervalle mit den Runtime-Settings
    abgleichen, nächste Laufzeiten für die API publizieren."""
    from app import jobs
    from app.services_settings import load_settings

    for job_id in JOB_FUNCS:
        if await jobs.pop_trigger(job_id):
            logger.info("Ad-hoc-Start: %s", job_id)
            asyncio.create_task(wrapped_job(job_id)())

    async with SessionLocal() as db:
        cfg = await load_settings(db, "scheduler")
        # Champion-Overrides (Strategie) im Worker-Prozess frisch halten
        from app.analysis.scoring import load_champion
        await load_champion(db)
    for job_id, spec in jobs.JOBS.items():
        setting = spec.get("setting")
        if not setting or _scheduler is None:
            continue
        if spec["unit"] == "time":
            parsed = _parse_time(cfg.get(setting))
            if parsed is None:
                continue
            desired: tuple = ("time", *parsed)
        elif spec["unit"] == "times":
            parsed_list = _parse_times(cfg.get(setting))
            if parsed_list is None:
                continue
            desired = ("times", tuple(parsed_list))
        else:
            try:
                value = int(float(cfg.get(setting)))
            except (TypeError, ValueError):
                continue
            desired = (spec["unit"], value)
        if _applied.get(job_id) == desired:
            continue
        try:
            if desired[0] == "times":
                _scheduler.reschedule_job(job_id, trigger=_times_trigger(list(desired[1])))
            elif desired[0] == "time":
                _scheduler.reschedule_job(job_id, trigger="cron",
                                          hour=desired[1], minute=desired[2])
            elif desired[1] <= 0:
                _scheduler.pause_job(job_id)  # 0 = aus
            else:
                kwargs = {"minutes": desired[1]} if desired[0] == "min" else {"days": desired[1]}
                _scheduler.reschedule_job(job_id, trigger="interval", **kwargs)
            _applied[job_id] = desired
            logger.info("Job %s umgeplant: %s", job_id, desired)
        except Exception as e:
            logger.warning("Umplanen von %s fehlgeschlagen: %s", job_id, e)

    if _scheduler is not None:
        next_map = {}
        for job_id in JOB_FUNCS:
            j = _scheduler.get_job(job_id)
            nrt = getattr(j, "next_run_time", None) if j else None
            next_map[job_id] = nrt.isoformat() if nrt else None
        await jobs.publish_next_runs(next_map)


def build_scheduler() -> AsyncIOScheduler:
    global _scheduler
    s = get_settings()
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(wrapped_job("sync_market"), "interval",
                      minutes=s.fetch_market_interval_min,
                      id="sync_market", max_instances=1, coalesce=True)
    scheduler.add_job(wrapped_job("sync_news"), "interval",
                      minutes=s.fetch_news_interval_min,
                      id="sync_news", max_instances=1, coalesce=True)
    scheduler.add_job(wrapped_job("analyze"), "interval",
                      minutes=s.analyze_interval_min,
                      id="analyze", max_instances=1, coalesce=True)
    scheduler.add_job(wrapped_job("scan_universe"), "interval",
                      minutes=s.scan_interval_min,
                      id="scan_universe", max_instances=1, coalesce=True)
    # Stündlich zusätzlich: Horizont-Exits + fällige Signal-Auswertungen,
    # unabhängig vom Analyse-Rhythmus
    scheduler.add_job(wrapped_job("paper_trading"), "interval", minutes=60,
                      id="paper_trading", max_instances=1, coalesce=True)
    hour, minute = _parse_time(s.discovery_time) or (2, 30)
    # Nachts, wenn US-Schlusskurse final sind und nichts anderes läuft
    scheduler.add_job(wrapped_job("discovery"), "cron", hour=hour, minute=minute,
                      id="discovery", max_instances=1, coalesce=True)
    digest_times = _parse_times(s.digest_times) or [(16, 45), (21, 15)]
    scheduler.add_job(wrapped_job("digest"), _times_trigger(digest_times),
                      id="digest", max_instances=1, coalesce=True)
    optimize_kwargs = {} if s.optimize_interval_days > 0 else {"next_run_time": None}
    scheduler.add_job(wrapped_job("auto_optimize"), "interval",
                      days=max(s.optimize_interval_days, 1),
                      id="auto_optimize", max_instances=1, coalesce=True,
                      **optimize_kwargs)  # 0 = pausiert starten

    from datetime import datetime, timedelta, timezone
    refresh_kwargs = (
        # kurz nach dem Start einmal laufen, damit die Index-Segmente
        # nicht erst in 30 Tagen entstehen
        {"next_run_time": datetime.now(timezone.utc) + timedelta(minutes=3)}
        if s.universe_refresh_days > 0 else {"next_run_time": None}
    )
    scheduler.add_job(wrapped_job("refresh_universe"), "interval",
                      days=max(s.universe_refresh_days, 1),
                      id="refresh_universe", max_instances=1, coalesce=True,
                      **refresh_kwargs)

    scheduler.add_job(job_tick, "interval", seconds=20,
                      id="tick", max_instances=1, coalesce=True)

    # Der erste Tick soll nur bei echten DB-Overrides umplanen — sonst
    # würde er z.B. den Initial-Refresh (+3 Min) sofort wegplanen.
    _applied.update({
        "sync_market": ("min", s.fetch_market_interval_min),
        "sync_news": ("min", s.fetch_news_interval_min),
        "analyze": ("min", s.analyze_interval_min),
        "scan_universe": ("min", s.scan_interval_min),
        "auto_optimize": ("days", s.optimize_interval_days),
        "refresh_universe": ("days", s.universe_refresh_days),
        "discovery": ("time", hour, minute),
        "digest": ("times", tuple(digest_times)),
    })
    _scheduler = scheduler
    return scheduler
