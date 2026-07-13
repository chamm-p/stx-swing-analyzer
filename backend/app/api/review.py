"""Review-API: Signalqualität (Trefferquote, Ø-Rendite) + Einzelauswertungen."""

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.signal_review import evaluate_signals, review_summary
from app.auth.deps import require_user
from app.database import get_db
from app.models import Signal

router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


@router.get("/review/summary")
async def get_summary(db: AsyncSession = Depends(get_db)):
    return await review_summary(db)


@router.get("/review/signals")
async def evaluated_signals(limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Signal).where(Signal.evaluated_at.isnot(None), Signal.eval_return_pct.isnot(None))
        .order_by(desc(Signal.evaluated_at)).limit(min(limit, 200))
    )
    return [{
        "id": str(s.id), "symbol": s.symbol, "ts": s.ts, "action": s.action,
        "confidence": s.confidence, "price_at_signal": s.price_at_signal,
        "horizon_days": s.horizon_days, "eval_price": s.eval_price,
        "eval_return_pct": s.eval_return_pct, "eval_hit": s.eval_hit,
        "evaluated_at": s.evaluated_at,
    } for s in result.scalars().all()]


@router.post("/review/run")
async def run_review(db: AsyncSession = Depends(get_db)):
    """Fällige Signale sofort auswerten (läuft sonst stündlich im Worker)."""
    return {"evaluated": await evaluate_signals(db)}
