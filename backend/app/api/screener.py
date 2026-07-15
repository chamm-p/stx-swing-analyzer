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

    Optional nach Segment gefiltert; "+" gruppiert (z.B. US+NASDAQ100 =
    alle US-Aktien, da Nasdaq-100-Mitglieder Vorrang vorm S&P-Label haben)."""
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
            # "+" dekodiert in Query-Strings zu Leerzeichen — beides akzeptieren
            segments = [t for t in segment.upper().replace(" ", "+").split("+") if t]
            q = q.where(UniverseSymbol.segment.in_(segments))
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

    # Kursänderung Vortag / 7 Tage je Symbol (aus Ohlcv, ein Batch-Query).
    from app.sources import yahoo
    deltas = await yahoo.price_deltas(db, [r.symbol for r, _, _ in rows])

    return {
        "run_at": last_run,
        "running": await screener.is_running(),
        "results": [{
            "symbol": r.symbol, "name": name, "segment": segment,
            "action": r.action, "technical_score": r.technical_score,
            "close": r.close, "snapshot": r.snapshot,
            "last_analysis_at": analysis_map.get(r.symbol),
            **deltas.get(r.symbol, {"change_1d": None, "change_7d": None}),
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


# ---------------------------------------------------------------- Discovery

@router.get("/discovery/top")
async def discovery_top(limit: int = 100, region: str | None = None,
                        db: AsyncSession = Depends(get_db)):
    """Top-Kandidaten des letzten Discovery-Scans (Breiten-Scan über die
    kompletten Börsenverzeichnisse, rein technisch)."""
    from app.analysis import discovery
    from app.models import AnalysisResult, DiscoveryResult

    last_run = await db.scalar(select(func.max(DiscoveryResult.run_at)))
    rows: list[DiscoveryResult] = []
    if last_run is not None:
        q = (
            select(DiscoveryResult)
            .where(DiscoveryResult.run_at == last_run)
            .order_by(desc(func.abs(DiscoveryResult.technical_score)))
            .limit(min(limit, 200))
        )
        if region:
            q = q.where(DiscoveryResult.region == region.upper())
        rows = (await db.execute(q)).scalars().all()

    analysis_map: dict = {}
    if rows:
        res = await db.execute(
            select(AnalysisResult.symbol, func.max(AnalysisResult.ts))
            .where(AnalysisResult.kind == "asset_review",
                   AnalysisResult.symbol.in_([r.symbol for r in rows]))
            .group_by(AnalysisResult.symbol)
        )
        analysis_map = {sym: ts for sym, ts in res.all()}

    return {
        "run_at": last_run,
        "running": await discovery.is_running(),
        "results": [{
            "symbol": r.symbol, "name": r.name, "segment": r.region,
            "action": r.action, "technical_score": r.technical_score,
            "close": r.close, "snapshot": r.snapshot,
            "change_1d": r.change_1d, "change_7d": r.change_7d,
            "avg_turnover": r.avg_turnover,
            "last_analysis_at": analysis_map.get(r.symbol),
        } for r in rows],
    }


async def _run_discovery_bg() -> None:
    from app.analysis import discovery
    async with SessionLocal() as db:
        try:
            await discovery.run_discovery(db)
        except Exception as e:
            logger.exception("Manueller Discovery-Scan fehlgeschlagen: %s", e)


@router.post("/discovery/run", status_code=202)
async def trigger_discovery():
    from app.analysis import discovery
    if await discovery.is_running():
        raise HTTPException(status_code=409, detail="Discovery-Scan läuft bereits")
    asyncio.create_task(_run_discovery_bg())
    return {"started": True}


# ---------------------------------------------------------------- Universum

@router.post("/universe/refresh")
async def refresh_universe(db: AsyncSession = Depends(get_db)):
    """Index-Mitgliedschaften (S&P 500, Nasdaq 100, DAX/MDAX/SDAX, Euro
    Stoxx 50) aus Wikipedia synchronisieren."""
    from app.sources.indices import refresh_indices
    try:
        return await refresh_indices(db)
    except Exception as e:
        logger.exception("Index-Refresh fehlgeschlagen: %s", e)
        raise HTTPException(status_code=502, detail=f"Index-Refresh fehlgeschlagen: {e}")


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
