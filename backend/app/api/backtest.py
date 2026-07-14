"""Backtest-API: Läufe starten, auflisten, Details, löschen."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_user
from app.backtest.runner import start_run
from app.database import get_db
from app.models import BacktestRun

router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


class BacktestRequest(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    segment: str | None = None  # US | DAX | CRYPTO | None = alle
    days: int = Field(default=730, ge=300, le=7300)
    backfill: bool = False
    platform_id: int | None = None
    params: dict = Field(default_factory=dict)  # StrategyConfig-Overrides
    # Walk-Forward
    mode: str = Field(default="single", pattern="^(single|walkforward)$")
    grid: dict = Field(default_factory=dict)  # {param: [werte…]}
    train_days: int = Field(default=365, ge=90, le=1825)
    test_days: int = Field(default=90, ge=30, le=365)
    min_trades: int = Field(default=20, ge=0, le=500)


@router.post("/backtest/run", status_code=202)
async def run_backtest_endpoint(payload: BacktestRequest):
    run_id = await start_run(payload.model_dump())
    return {"id": str(run_id), "status": "running"}


def _summary(run: BacktestRun) -> dict:
    m = run.metrics or {}
    return {
        "id": str(run.id), "created_at": run.created_at, "status": run.status,
        "label": run.label, "segment": run.segment or "alle", "days": run.days,
        "params": run.params, "error": run.error,
        "total_return_pct": m.get("total_return_pct"),
        "benchmark_return_pct": m.get("benchmark_return_pct"),
        "sharpe": m.get("sharpe"),
        "max_drawdown_pct": m.get("max_drawdown_pct"),
        "num_trades": m.get("num_trades"),
        "win_rate": m.get("win_rate"),
        "profit_factor": m.get("profit_factor"),
        "fees_total": m.get("fees_total"),
    }


@router.get("/backtest/runs")
async def list_runs(limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(BacktestRun).order_by(desc(BacktestRun.created_at)).limit(min(limit, 200))
    )
    return [_summary(r) for r in result.scalars().all()]


@router.get("/backtest/runs/{run_id}")
async def run_detail(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    run = await db.get(BacktestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Lauf nicht gefunden")
    return {
        **_summary(run),
        "metrics": run.metrics,
        "equity": run.equity or [],
        "benchmark": run.benchmark or [],
        "trades": run.trades or [],
        "warnings": run.warnings or [],
    }


class ApplyRequest(BaseModel):
    name: str | None = Field(default=None, max_length=100)


@router.post("/backtest/runs/{run_id}/apply", status_code=201)
async def apply_as_challenger(run_id: uuid.UUID, payload: ApplyRequest,
                              db: AsyncSession = Depends(get_db)):
    """Gewinner-Parameter als Challenger-Auto-Portfolio übernehmen.

    Das Portfolio papertradet mit EIGENEM Scoring (Strategie-Modus des
    Auto-Traders) parallel zum Champion. Bei Walk-Forward-Läufen wird
    der am häufigsten gewählte Parametersatz übernommen. Beobachten ist
    aus (kein LLM-/Watchlist-Flooding); Ablösung bleibt manuell."""
    import ast

    from app.analysis.auto_trader import STRATEGY_KEYS
    from app.backtest.params import StrategyConfig
    from app.models import Portfolio

    run = await db.get(BacktestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Lauf nicht gefunden")
    if run.status != "done":
        raise HTTPException(status_code=409, detail="Lauf ist nicht abgeschlossen")

    stored = dict(run.params or {})
    overrides = {k: v for k, v in stored.items() if isinstance(v, (int, float))}
    if (run.metrics or {}).get("mode") == "walkforward":
        wins = (run.metrics or {}).get("param_wins") or {}
        if wins:
            best = max(wins.items(), key=lambda kv: kv[1])[0]
            try:
                overrides.update(ast.literal_eval(best))
            except (ValueError, SyntaxError):
                pass

    defaults = StrategyConfig().to_dict()
    strategy = {k: overrides.get(k, defaults[k]) for k in STRATEGY_KEYS}
    start_capital = float(overrides.get("start_capital", defaults["start_capital"]))
    name = (payload.name or f"Challenger {run.label or str(run.id)[:8]}")[:100]

    portfolio = Portfolio(
        name=name, kind="auto",
        platform_id=stored.get("platform_id"),
        watch_enabled=False,
        cash=start_capital,
        config={
            "start_capital": start_capital,
            "max_per_trade": float(overrides.get("position_size", defaults["position_size"])),
            "max_positions": int(overrides.get("max_positions", defaults["max_positions"])),
            "min_confidence": 0.0,
            "use_screener": False,
            "enabled": True,
            "strategy": strategy,
            "source_run": str(run.id),
        },
    )
    db.add(portfolio)
    await db.commit()
    return {"portfolio_id": portfolio.id, "name": name, "strategy": strategy}


@router.delete("/backtest/runs/{run_id}")
async def delete_run(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    run = await db.get(BacktestRun, run_id)
    if run:
        await db.delete(run)
        await db.commit()
    return {"ok": True}
