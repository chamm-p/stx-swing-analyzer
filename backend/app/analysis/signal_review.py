"""Signal-Review: bewertet JEDES Signal nach Ablauf seines Horizonts.

Unabhängig davon, ob es gehandelt wurde: erster Tagesschluss nach
``ts + horizon_days`` vs. Kurs zum Signalzeitpunkt. Treffer-Definition:
BUY → Rendite > 0, SELL → Rendite < 0, HOLD → nur Drift (kein Hit).
Grundlage für datenbasiertes Tuning der Scoring-Parameter (Phase 3:
Backtesting/Champion-Challenger).
"""

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Asset, Ohlcv, Signal, utcnow

logger = logging.getLogger(__name__)


async def _close_at_or_after(db: AsyncSession, symbol: str, target) -> float | None:
    result = await db.execute(
        select(Ohlcv.close).where(Ohlcv.symbol == symbol, Ohlcv.ts >= target)
        .order_by(Ohlcv.ts).limit(1)
    )
    return result.scalar()


async def evaluate_signals(db: AsyncSession) -> int:
    """Wertet fällige, noch unbewertete Signale aus. Liefert Anzahl."""
    now = utcnow()
    result = await db.execute(
        select(Signal).where(Signal.evaluated_at.is_(None)).order_by(Signal.ts)
    )
    done = 0
    for s in result.scalars().all():
        target = s.ts + timedelta(days=s.horizon_days or 14)
        if now < target:
            continue  # Horizont noch nicht abgelaufen
        base = s.price_at_signal
        if not base:
            s.evaluated_at = now  # ohne Basiskurs nicht bewertbar
            continue
        eval_price = await _close_at_or_after(db, s.symbol, target)
        if eval_price is None:
            continue  # Kurs nach Horizont noch nicht vorhanden — später erneut
        ret = (eval_price - base) / base * 100
        s.eval_price = round(eval_price, 4)
        s.eval_return_pct = round(ret, 2)
        if s.action == "BUY":
            s.eval_hit = ret > 0
        elif s.action == "SELL":
            s.eval_hit = ret < 0
        s.evaluated_at = now
        done += 1
    if done:
        await db.commit()
        logger.info("Signal-Review: %d Signale ausgewertet", done)
    return done


async def review_summary(db: AsyncSession) -> dict:
    """Aggregierte Signalqualität nach Aktion und Asset-Klasse."""
    result = await db.execute(
        select(Signal, Asset.asset_type)
        .outerjoin(Asset, Asset.symbol == Signal.symbol)
        .where(Signal.evaluated_at.isnot(None), Signal.eval_return_pct.isnot(None))
    )
    rows = result.all()

    groups: dict[tuple, dict] = {}
    for s, asset_type in rows:
        key = (s.action, asset_type or "stock")
        g = groups.setdefault(key, {"count": 0, "hits": 0, "hit_total": 0, "returns": []})
        g["count"] += 1
        g["returns"].append(s.eval_return_pct)
        if s.eval_hit is not None:
            g["hit_total"] += 1
            g["hits"] += 1 if s.eval_hit else 0

    pending = await db.scalar(
        select(Signal.id).where(Signal.evaluated_at.is_(None)).limit(1)
    )
    return {
        "evaluated_count": len(rows),
        "has_pending": pending is not None,
        "groups": [{
            "action": action,
            "asset_type": asset_type,
            "count": g["count"],
            "hit_rate": round(g["hits"] / g["hit_total"], 3) if g["hit_total"] else None,
            "avg_return_pct": round(sum(g["returns"]) / len(g["returns"]), 2),
        } for (action, asset_type), g in sorted(groups.items())],
    }
