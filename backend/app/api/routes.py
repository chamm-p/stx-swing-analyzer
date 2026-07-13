"""REST-API: Dashboard, Signale, Watchlist, Assets, Quellen, Analysen.

Alle Routen erfordern einen angemeldeten User (Single-User-Gate).
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.pipeline import run_for_symbol
from app.auth.deps import require_user
from app.database import get_db
from app.models import (
    AnalysisResult, Asset, DataSource, NewsArticle, Signal, WatchlistItem,
)
from app.processing.indicators import compute_indicators
from app.sources import yahoo

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


# ---------------------------------------------------------------- Watchlist

class WatchlistAdd(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    notes: str | None = None


class WatchlistUpdate(BaseModel):
    notes: str | None = None
    alert_enabled: bool | None = None
    min_confidence: float | None = Field(default=None, ge=0, le=1)


@router.get("/watchlist")
async def get_watchlist(db: AsyncSession = Depends(get_db)):
    """Manuelle Einträge + abgeleitete Symbole aus beobachteten Portfolios."""
    from app.analysis.watch_scope import DERIVED_MIN_CONFIDENCE, derived_symbols

    async def entry(symbol: str, asset: Asset | None, base: dict) -> dict:
        last_signal = await db.scalar(
            select(Signal).where(Signal.symbol == symbol).order_by(desc(Signal.ts)).limit(1)
        )
        last_news = await db.scalar(
            select(func.max(NewsArticle.published_at)).where(NewsArticle.symbols.any(symbol))
        )
        return {
            "symbol": symbol,
            "name": asset.name if asset else None,
            "asset_type": asset.asset_type if asset else "stock",
            "currency": asset.currency if asset else None,
            "last_news_at": last_news,
            "last_signal": _signal_dict(last_signal) if last_signal else None,
            **base,
        }

    result = await db.execute(
        select(WatchlistItem, Asset).join(Asset, Asset.symbol == WatchlistItem.symbol)
        .order_by(WatchlistItem.added_at)
    )
    out = []
    manual_symbols = set()
    for item, asset in result.all():
        manual_symbols.add(item.symbol)
        out.append(await entry(item.symbol, asset, {
            "source": "watchlist",
            "alert_enabled": item.alert_enabled,
            "min_confidence": item.min_confidence,
            "notes": item.notes,
            "added_at": item.added_at,
        }))

    for symbol, pf_names in (await derived_symbols(db)).items():
        if symbol in manual_symbols:
            continue
        asset = await db.get(Asset, symbol)
        out.append(await entry(symbol, asset, {
            "source": "portfolio",
            "portfolios": pf_names,
            "alert_enabled": True,
            "min_confidence": DERIVED_MIN_CONFIDENCE,
            "notes": None,
            "added_at": None,
        }))
    return out


@router.post("/watchlist", status_code=201)
async def add_to_watchlist(payload: WatchlistAdd, db: AsyncSession = Depends(get_db)):
    symbol = payload.symbol.strip().upper()
    existing = await db.get(WatchlistItem, symbol)
    if existing:
        raise HTTPException(status_code=409, detail=f"{symbol} ist bereits auf der Watchlist")
    try:
        await yahoo.ensure_asset(db, symbol)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Symbol {symbol} konnte nicht aufgelöst werden: {e}")
    db.add(WatchlistItem(symbol=symbol, notes=payload.notes))
    await db.commit()
    # Kursdaten direkt initial laden, damit das Chart sofort etwas zeigt
    try:
        await yahoo.sync_ohlcv(db, symbol)
    except Exception as e:
        logger.warning("Initialer Kurs-Sync %s fehlgeschlagen: %s", symbol, e)
    return {"symbol": symbol, "ok": True}


@router.patch("/watchlist/{symbol}")
async def update_watchlist(symbol: str, payload: WatchlistUpdate, db: AsyncSession = Depends(get_db)):
    item = await db.get(WatchlistItem, symbol.upper())
    if not item:
        raise HTTPException(status_code=404, detail="Nicht auf der Watchlist")
    if payload.notes is not None:
        item.notes = payload.notes
    if payload.alert_enabled is not None:
        item.alert_enabled = payload.alert_enabled
    if payload.min_confidence is not None:
        item.min_confidence = payload.min_confidence
    await db.commit()
    return {"ok": True}


@router.delete("/watchlist/{symbol}")
async def remove_from_watchlist(symbol: str, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(WatchlistItem).where(WatchlistItem.symbol == symbol.upper()))
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- Signale

def _signal_dict(s: Signal) -> dict:
    return {
        "id": str(s.id), "symbol": s.symbol, "ts": s.ts, "action": s.action,
        "confidence": s.confidence, "composite_score": s.composite_score,
        "technical_score": s.technical_score, "sentiment_score": s.sentiment_score,
        "fundamental_score": s.fundamental_score, "rationale": s.rationale,
        "horizon_days": s.horizon_days, "price_at_signal": s.price_at_signal,
        "indicators": s.indicators, "delivered": s.delivered,
    }


@router.get("/signals")
async def list_signals(symbol: str | None = None, limit: int = 50,
                       db: AsyncSession = Depends(get_db)):
    q = select(Signal).order_by(desc(Signal.ts)).limit(min(limit, 200))
    if symbol:
        q = q.where(Signal.symbol == symbol.upper())
    result = await db.execute(q)
    return [_signal_dict(s) for s in result.scalars().all()]


@router.post("/signals/run/{symbol}")
async def trigger_analysis(symbol: str, db: AsyncSession = Depends(get_db)):
    """Manuelle Analyse eines Symbols (synchron — kann je nach LLM dauern).

    Zulässig für die effektive Watchlist: manuelle Einträge UND offene
    Positionen aus Portfolios mit aktivem „Beobachten"-Schalter."""
    from app.analysis.watch_scope import effective_symbols

    symbol = symbol.upper()
    if symbol not in await effective_symbols(db):
        raise HTTPException(
            status_code=404,
            detail="Weder auf der Watchlist noch in einem beobachteten Portfolio",
        )
    await yahoo.sync_ohlcv(db, symbol)
    signal = await run_for_symbol(db, symbol)
    return {"created": signal is not None, "signal": _signal_dict(signal) if signal else None}


# ---------------------------------------------------------------- Assets / Charts

@router.get("/assets/{symbol}/chart")
async def asset_chart(symbol: str, days: int = 365, db: AsyncSession = Depends(get_db)):
    symbol = symbol.upper()
    df = await yahoo.load_ohlcv_df(db, symbol, days=min(days, 730))
    if df.empty:
        return {"candles": [], "indicators": {}, "signals": []}

    indicators = compute_indicators(df)
    candles = [{
        "time": ts.strftime("%Y-%m-%d"),
        "open": row["open"], "high": row["high"], "low": row["low"], "close": row["close"],
        "volume": row["volume"],
    } for ts, row in df.iterrows()]

    series_out = {}
    for name, series in indicators["series"].items():
        series_out[name] = [
            {"time": ts.strftime("%Y-%m-%d"), "value": round(float(v), 4)}
            for ts, v in series.items() if v == v  # NaN-Filter
        ]

    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(Signal).where(Signal.symbol == symbol, Signal.ts >= since).order_by(Signal.ts)
    )
    signals = [_signal_dict(s) for s in result.scalars().all()]
    return {"candles": candles, "indicators": series_out,
            "snapshot": indicators["snapshot"], "signals": signals}


_PROFILE_CACHE_TTL = 86400  # Stammdaten ändern sich selten — 1x/Tag reicht

_TRANSLATE_SYSTEM = (
    "Du bist Übersetzer für Finanztexte. Übersetze den folgenden "
    "Unternehmens-Beschreibungstext präzise ins Deutsche. Antworte NUR mit "
    "der Übersetzung, ohne Kommentar."
)


@router.get("/assets/{symbol}/profile")
async def asset_profile(symbol: str, db: AsyncSession = Depends(get_db)):
    """Unternehmensprofil (Sektor, Kennzahlen, Beschreibung) — Redis-gecacht.

    Ist ein LLM konfiguriert, wird die englische Yahoo-Beschreibung
    zusätzlich als ``summary_de`` übersetzt (fail-soft auf Englisch).
    """
    import json as _json

    from app.llm.client import LLMClient, LLMError
    from app.services_redis import get_redis
    from app.services_settings import load_settings

    symbol = symbol.upper()
    r = get_redis()
    cache_key = f"profile:{symbol}"
    cached = await r.get(cache_key)
    if cached:
        return _json.loads(cached)

    try:
        profile = await yahoo.fetch_profile(symbol)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Profil für {symbol} nicht abrufbar: {e}")
    profile["symbol"] = symbol

    llm_cfg = await load_settings(db, "llm")
    if profile.get("summary") and llm_cfg.get("api_key"):
        try:
            profile["summary_de"] = await LLMClient(llm_cfg).complete(
                _TRANSLATE_SYSTEM, profile["summary"][:6000]
            )
        except LLMError as e:
            logger.warning("Profil-Übersetzung %s fehlgeschlagen: %s", symbol, e)

    await r.set(cache_key, _json.dumps(profile), ex=_PROFILE_CACHE_TTL)
    return profile


@router.get("/assets/{symbol}/news")
async def asset_news(symbol: str, limit: int = 30, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(NewsArticle).where(NewsArticle.symbols.any(symbol.upper()))
        .order_by(desc(NewsArticle.published_at)).limit(min(limit, 100))
    )
    return [{
        "id": str(a.id), "title": a.title, "url": a.url, "summary": a.summary,
        "source": a.source_name, "published_at": a.published_at,
        "sentiment_score": a.sentiment_score, "sentiment_label": a.sentiment_label,
        "sentiment_rationale": a.sentiment_rationale,
    } for a in result.scalars().all()]


@router.get("/assets/{symbol}/analyses")
async def asset_analyses(symbol: str, limit: int = 20, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AnalysisResult).where(AnalysisResult.symbol == symbol.upper(),
                                     AnalysisResult.kind == "asset_review")
        .order_by(desc(AnalysisResult.ts)).limit(min(limit, 50))
    )
    return [{
        "id": str(r.id), "ts": r.ts, "kind": r.kind, "model": r.model, "payload": r.payload,
    } for r in result.scalars().all()]


# ---------------------------------------------------------------- Datenquellen

class SourceCreate(BaseModel):
    name: str
    url: str
    kind: str = "news_rss"
    enabled: bool = True
    priority: int = 100


class SourceUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    enabled: bool | None = None
    priority: int | None = None


@router.get("/sources")
async def list_sources(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DataSource).order_by(DataSource.priority, DataSource.id))
    return [{
        "id": s.id, "kind": s.kind, "name": s.name, "url": s.url, "enabled": s.enabled,
        "priority": s.priority, "last_fetch_at": s.last_fetch_at, "last_error": s.last_error,
    } for s in result.scalars().all()]


@router.post("/sources", status_code=201)
async def create_source(payload: SourceCreate, db: AsyncSession = Depends(get_db)):
    source = DataSource(**payload.model_dump())
    db.add(source)
    await db.commit()
    return {"id": source.id, "ok": True}


@router.patch("/sources/{source_id}")
async def update_source(source_id: int, payload: SourceUpdate, db: AsyncSession = Depends(get_db)):
    source = await db.get(DataSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Quelle nicht gefunden")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(source, field, value)
    await db.commit()
    return {"ok": True}


@router.delete("/sources/{source_id}")
async def delete_source(source_id: int, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(DataSource).where(DataSource.id == source_id))
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- Dashboard

@router.get("/dashboard")
async def dashboard(db: AsyncSession = Depends(get_db)):
    from app.analysis.watch_scope import effective_symbols

    signals = await db.execute(select(Signal).order_by(desc(Signal.ts)).limit(20))
    watch_count = len(await effective_symbols(db))
    news_24h = await db.scalar(
        select(func.count()).select_from(NewsArticle)
        .where(NewsArticle.published_at >= datetime.now(timezone.utc) - timedelta(hours=24))
    )
    return {
        "watchlist_count": watch_count,
        "news_last_24h": news_24h,
        "recent_signals": [_signal_dict(s) for s in signals.scalars().all()],
    }
