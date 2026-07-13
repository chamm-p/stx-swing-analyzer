"""Screener-API: Top-Signale aus dem Universum + Universum-Verwaltung."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import screener
from app.auth.deps import require_user
from app.database import SessionLocal, get_db
from app.models import ScreenerResult, UniverseSymbol

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


@router.get("/screener/top")
async def top_signals(limit: int = 25, segment: str | None = None,
                      db: AsyncSession = Depends(get_db)):
    """Bestenliste des letzten Scans, sortiert nach Signalstärke.

    Optional nach Segment gefiltert (z.B. CRYPTO, DAX, US)."""
    last_run = await db.scalar(select(func.max(ScreenerResult.run_at)))
    rows = []
    if last_run is not None:
        q = (
            select(ScreenerResult, UniverseSymbol.name, UniverseSymbol.segment)
            .outerjoin(UniverseSymbol, UniverseSymbol.symbol == ScreenerResult.symbol)
            .where(ScreenerResult.run_at == last_run)
            .order_by(desc(func.abs(ScreenerResult.technical_score)))
            .limit(min(limit, 100))
        )
        if segment:
            q = q.where(UniverseSymbol.segment == segment.upper())
        result = await db.execute(q)
        rows = result.all()

    # Analyse-Frische je Symbol (letztes LLM-Review) für die Ampel
    analysis_map: dict = {}
    if rows:
        from app.models import AnalysisResult
        res = await db.execute(
            select(AnalysisResult.symbol, func.max(AnalysisResult.ts))
            .where(AnalysisResult.kind == "asset_review",
                   AnalysisResult.symbol.in_([r.symbol for r, _, _ in rows]))
            .group_by(AnalysisResult.symbol)
        )
        analysis_map = {sym: ts for sym, ts in res.all()}

    return {
        "run_at": last_run,
        "running": await screener.is_running(),
        "results": [{
            "symbol": r.symbol, "name": name, "segment": segment,
            "action": r.action, "technical_score": r.technical_score,
            "close": r.close, "snapshot": r.snapshot,
            "last_analysis_at": analysis_map.get(r.symbol),
        } for r, name, segment in rows],
    }


async def _run_scan_bg() -> None:
    async with SessionLocal() as db:
        try:
            await screener.scan_universe(db)
        except Exception as e:
            logger.exception("Manueller Screener-Scan fehlgeschlagen: %s", e)


@router.post("/screener/run", status_code=202)
async def trigger_scan():
    if await screener.is_running():
        raise HTTPException(status_code=409, detail="Scan läuft bereits")
    asyncio.create_task(_run_scan_bg())
    return {"started": True}


# ---------------------------------------------------------------- Universum

class UniverseAdd(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    name: str | None = None
    segment: str = "custom"


@router.get("/universe")
async def list_universe(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(UniverseSymbol).order_by(UniverseSymbol.segment, UniverseSymbol.symbol)
    )
    return [{
        "symbol": u.symbol, "name": u.name, "segment": u.segment, "active": u.active,
    } for u in result.scalars().all()]


@router.post("/universe", status_code=201)
async def add_universe_symbol(payload: UniverseAdd, db: AsyncSession = Depends(get_db)):
    symbol = payload.symbol.strip().upper()
    existing = await db.get(UniverseSymbol, symbol)
    if existing:
        raise HTTPException(status_code=409, detail=f"{symbol} ist bereits im Universum")
    db.add(UniverseSymbol(symbol=symbol, name=payload.name, segment=payload.segment))
    await db.commit()
    return {"ok": True}


@router.delete("/universe/{symbol}")
async def remove_universe_symbol(symbol: str, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(UniverseSymbol).where(UniverseSymbol.symbol == symbol.upper()))
    await db.commit()
    return {"ok": True}
