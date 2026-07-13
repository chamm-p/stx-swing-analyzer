"""Backtest-Runner: führt Läufe im Hintergrund aus und persistiert sie."""

import asyncio
import logging
import uuid

import pandas as pd
from sqlalchemy import select

from app.backtest.engine import run_backtest
from app.backtest.metrics import compute_metrics
from app.backtest.params import StrategyConfig
from app.database import SessionLocal
from app.models import Asset, BacktestRun, TradingPlatform, UniverseSymbol

logger = logging.getLogger(__name__)

# Nur diese Keys dürfen die StrategyConfig überschreiben
_PARAM_KEYS = set(StrategyConfig().to_dict().keys()) - {"fees"}

MAX_EQUITY_POINTS = 500


def _downsample(series) -> list[dict]:
    step = max(len(series) // MAX_EQUITY_POINTS, 1)
    points = series.iloc[::step]
    if len(series) and series.index[-1] not in points.index:
        points = pd.concat([points, series.iloc[[-1]]])
    return [{"time": ts.strftime("%Y-%m-%d"), "value": round(float(v), 2)}
            for ts, v in points.items()]


async def start_run(payload: dict) -> uuid.UUID:
    """Legt den Lauf an und startet die Ausführung als Hintergrund-Task."""
    params = {k: v for k, v in (payload.get("params") or {}).items()
              if k in _PARAM_KEYS and v is not None}
    async with SessionLocal() as db:
        run = BacktestRun(
            status="running",
            label=payload.get("label"),
            segment=payload.get("segment"),
            days=int(payload.get("days") or 730),
            params={**params,
                    "platform_id": payload.get("platform_id"),
                    "backfill": bool(payload.get("backfill"))},
        )
        db.add(run)
        await db.commit()
        run_id = run.id
    asyncio.create_task(_execute(run_id))
    return run_id


async def _execute(run_id: uuid.UUID) -> None:
    from app.sources.yahoo import backfill_ohlcv, load_ohlcv_df

    async with SessionLocal() as db:
        run = await db.get(BacktestRun, run_id)
        if run is None:
            return
        try:
            stored = dict(run.params or {})
            platform_id = stored.pop("platform_id", None)
            do_backfill = stored.pop("backfill", False)
            overrides = {k: v for k, v in stored.items() if k in _PARAM_KEYS}

            q = select(UniverseSymbol).where(UniverseSymbol.active == True)  # noqa: E712
            if run.segment:
                q = q.where(UniverseSymbol.segment == run.segment)
            symbols = [u.symbol for u in (await db.execute(q)).scalars().all()]
            if not symbols:
                raise RuntimeError("Keine Universum-Symbole für dieses Segment")

            if do_backfill:
                for symbol in symbols + ["SPY"]:
                    try:
                        await backfill_ohlcv(db, symbol, run.days)
                    except Exception as e:
                        logger.warning("Backfill %s fehlgeschlagen: %s", symbol, e)

            data, currencies = {}, {}
            for symbol in symbols:
                df = await load_ohlcv_df(db, symbol, days=run.days)
                if len(df) > 250:
                    data[symbol] = df
                    asset = await db.get(Asset, symbol)
                    if asset and asset.currency:
                        currencies[symbol] = asset.currency
            if not data:
                raise RuntimeError("Zu wenig Kurshistorie — Backfill aktivieren?")

            fees = None
            if platform_id:
                platform = await db.get(TradingPlatform, platform_id)
                fees = platform.fees if platform else None

            config = StrategyConfig(**overrides, fees=fees)
            result = await asyncio.to_thread(run_backtest, data, config, currencies)
            metrics = compute_metrics(result)
            metrics["symbols_tested"] = len(data)

            # Benchmark: SPY auf Startkapital normiert über denselben Zeitraum
            benchmark: list[dict] = []
            spy = await load_ohlcv_df(db, "SPY", days=run.days)
            if not spy.empty and len(result.equity) > 1:
                window = spy.loc[(spy.index >= result.equity.index[0]) &
                                 (spy.index <= result.equity.index[-1]), "close"]
                if len(window) > 1:
                    base = float(window.iloc[0])
                    norm = window / base * config.start_capital
                    benchmark = _downsample(norm)
                    metrics["benchmark_return_pct"] = round(
                        (float(window.iloc[-1]) / base - 1) * 100, 2)

            run.metrics = metrics
            run.equity = _downsample(result.equity) if len(result.equity) else []
            run.benchmark = benchmark
            run.warnings = result.warnings
            run.trades = [{
                "symbol": t.symbol,
                "entry_date": t.entry_date.strftime("%Y-%m-%d"),
                "entry_price": round(t.entry_price, 4),
                "quantity": t.quantity,
                "exit_date": t.exit_date.strftime("%Y-%m-%d") if t.exit_date else None,
                "exit_price": round(t.exit_price, 4) if t.exit_price else None,
                "reason": t.reason,
                "fees": round(t.fee_buy + t.fee_sell, 2),
                "pnl": round(t.pnl, 2) if t.pnl is not None else None,
            } for t in result.trades]
            run.status = "done"
            logger.info("Backtest %s fertig: %s Trades, Rendite %s%%",
                        run_id, metrics.get("num_trades"), metrics.get("total_return_pct"))
        except Exception as e:
            logger.exception("Backtest %s fehlgeschlagen", run_id)
            run.status = "error"
            run.error = str(e)[:500]
        await db.commit()
