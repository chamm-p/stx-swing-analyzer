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

# Segmentrichtige Benchmarks — SPY für einen DAX-Test wäre unfair
BENCHMARKS = {"US": "SPY", "DAX": "^GDAXI", "CRYPTO": "BTC-USD"}

# Auto-Optimierung: das System erkundet den Parameterraum selbst
AUTO_GRID = {
    "threshold": [0.30, 0.35, 0.40, 0.45],
    "target_atr_factor": [1.5, 2.0, 2.5],
    "stop_atr_factor": [1.0, 1.5, 2.0],
}  # 36 Kombinationen


def benchmark_symbol(segment: str | None) -> str:
    return BENCHMARKS.get(segment or "US", "SPY")


def _downsample(series) -> list[dict]:
    step = max(len(series) // MAX_EQUITY_POINTS, 1)
    points = series.iloc[::step]
    if len(series) and series.index[-1] not in points.index:
        points = pd.concat([points, series.iloc[[-1]]])
    return [{"time": ts.strftime("%Y-%m-%d"), "value": round(float(v), 2)}
            for ts, v in points.items()]



async def _load_benchmark(db, bench_symbol: str, days: int):
    """Benchmark-Kurse laden; fehlen sie (z.B. ^GDAXI), einmalig nachziehen."""
    from app.sources.yahoo import backfill_ohlcv, load_ohlcv_df
    df = await load_ohlcv_df(db, bench_symbol, days=days)
    if df.empty:
        try:
            await backfill_ohlcv(db, bench_symbol, days)
            df = await load_ohlcv_df(db, bench_symbol, days=days)
        except Exception as e:
            logger.warning("Benchmark %s nicht ladbar: %s", bench_symbol, e)
    return df


async def start_run(payload: dict) -> uuid.UUID:
    """Legt den Lauf an und startet die Ausführung als Hintergrund-Task."""
    params = {k: v for k, v in (payload.get("params") or {}).items()
              if k in _PARAM_KEYS and v is not None}
    grid = {k: v for k, v in (payload.get("grid") or {}).items()
            if k in _PARAM_KEYS and isinstance(v, list) and v}
    async with SessionLocal() as db:
        run = BacktestRun(
            status="running",
            label=payload.get("label"),
            segment=payload.get("segment"),
            days=int(payload.get("days") or 730),
            params={**params,
                    "platform_id": payload.get("platform_id"),
                    "backfill": bool(payload.get("backfill")),
                    "mode": payload.get("mode") or "single",
                    "grid": grid,
                    "train_days": int(payload.get("train_days") or 365),
                    "test_days": int(payload.get("test_days") or 90),
                    "min_trades": int(payload.get("min_trades") or 20),
                    "min_train_score": payload.get("min_train_score", 0.0)},
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
            mode = stored.pop("mode", "single")
            grid = stored.pop("grid", {}) or {}
            train_days = stored.pop("train_days", 365)
            test_days = stored.pop("test_days", 90)
            min_trades = stored.pop("min_trades", 20)
            min_train_score = stored.pop("min_train_score", 0.0)
            overrides = {k: v for k, v in stored.items() if k in _PARAM_KEYS}

            q = select(UniverseSymbol).where(UniverseSymbol.active == True)  # noqa: E712
            if run.segment:
                q = q.where(UniverseSymbol.segment == run.segment)
            symbols = [u.symbol for u in (await db.execute(q)).scalars().all()]
            if not symbols:
                raise RuntimeError("Keine Universum-Symbole für dieses Segment")

            bench_symbol = benchmark_symbol(run.segment)
            if do_backfill:
                for symbol in symbols + [bench_symbol]:
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

            if mode in ("walkforward", "optimize"):
                from app.backtest.walkforward import walk_forward
                if mode == "optimize":
                    # Auto-Optimierung: System-Grid + universumsgerechter
                    # Trade-Guard (kleine Universen erzeugen weniger Trades)
                    grid = AUTO_GRID
                    min_trades = min(min_trades, max(5, len(symbols) // 4))
                base_params = StrategyConfig(**overrides).to_dict()
                base_params.pop("fees", None)
                wf = await asyncio.to_thread(
                    walk_forward, data, base_params, grid,
                    currencies=currencies, fees=fees,
                    train_days=train_days, test_days=test_days,
                    min_trades=min_trades,
                    min_train_score=min_train_score,
                )
                metrics = {**wf["oos"], "mode": mode,
                           "symbols_tested": len(data),
                           "windows": wf["windows"],
                           "param_wins": wf["param_wins"]}
                # Segment-Benchmark über den Out-of-Sample-Zeitraum
                if wf["equity"]:
                    spy = await _load_benchmark(db, bench_symbol, run.days)
                    if not spy.empty:
                        start_ts = pd.Timestamp(wf["equity"][0]["time"], tz="UTC")
                        end_ts = pd.Timestamp(wf["equity"][-1]["time"], tz="UTC")
                        window = spy.loc[(spy.index >= start_ts) & (spy.index <= end_ts), "close"]
                        if len(window) > 1:
                            base = float(window.iloc[0])
                            start_cap = float(overrides.get("start_capital") or 10_000)
                            run.benchmark = _downsample(window / base * start_cap)
                            metrics["benchmark_return_pct"] = round(
                                (float(window.iloc[-1]) / base - 1) * 100, 2)
                            metrics["benchmark_symbol"] = bench_symbol
                # WICHTIG: Zuweisung NACH allen Mutationen — der SPY-SELECT
                # triggert einen Autoflush, spätere Dict-Mutationen würden
                # das JSONB-Feld nicht erneut dirty markieren
                run.metrics = metrics
                run.equity = wf["equity"]
                run.trades = []
                run.warnings = []
                run.status = "done"
                logger.info("Walk-Forward %s fertig: %s Fenster, OOS %s%%",
                            run_id, metrics.get("windows_tested"),
                            metrics.get("total_return_pct"))
                await db.commit()
                return

            config = StrategyConfig(**overrides, fees=fees)
            result = await asyncio.to_thread(run_backtest, data, config, currencies)
            metrics = compute_metrics(result)
            metrics["symbols_tested"] = len(data)

            # Benchmark: Segment-Index auf Startkapital normiert
            benchmark: list[dict] = []
            spy = await _load_benchmark(db, bench_symbol, run.days)
            if not spy.empty and len(result.equity) > 1:
                window = spy.loc[(spy.index >= result.equity.index[0]) &
                                 (spy.index <= result.equity.index[-1]), "close"]
                if len(window) > 1:
                    base = float(window.iloc[0])
                    norm = window / base * config.start_capital
                    benchmark = _downsample(norm)
                    metrics["benchmark_return_pct"] = round(
                        (float(window.iloc[-1]) / base - 1) * 100, 2)
                    metrics["benchmark_symbol"] = bench_symbol

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
