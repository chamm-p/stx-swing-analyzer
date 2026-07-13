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
    AnalysisResult, Asset, CustomEvent, DataSource, NewsArticle, Signal,
    WatchlistItem,
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
        last_analysis = await db.scalar(
            select(func.max(AnalysisResult.ts)).where(
                AnalysisResult.symbol == symbol, AnalysisResult.kind == "asset_review")
        )
        return {
            "symbol": symbol,
            "last_analysis_at": last_analysis,
            "name": asset.name if asset else None,
            "asset_type": asset.asset_type if asset else "stock",
            "currency": asset.currency if asset else None,
            "last_close": await yahoo.latest_close(db, symbol),
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
    asset_existed = await db.get(Asset, symbol) is not None
    try:
        await yahoo.ensure_asset(db, symbol)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Symbol {symbol} konnte nicht aufgelöst werden: {e}")
    # Kursdaten laden — und Tippfehler-Symbole ablehnen statt still anlegen
    try:
        await yahoo.sync_ohlcv(db, symbol)
    except Exception as e:
        logger.warning("Initialer Kurs-Sync %s fehlgeschlagen: %s", symbol, e)
    if await yahoo.latest_close(db, symbol) is None:
        if not asset_existed:
            asset = await db.get(Asset, symbol)
            if asset:
                await db.delete(asset)
                await db.commit()
        raise HTTPException(status_code=422,
                            detail=f"Keine Kursdaten für {symbol} — Symbol prüfen (Yahoo-Notation, z.B. CSCO, SAP.DE)")
    db.add(WatchlistItem(symbol=symbol, notes=payload.notes))
    await db.commit()
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
        "target_price": s.target_price, "stop_price": s.stop_price,
        "risk_reward": s.risk_reward, "analyst_target": s.analyst_target,
        "analyst_count": s.analyst_count,
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


_ANALYZE_STATUS_TTL = 600


async def _run_analysis_bg(symbol: str) -> None:
    """Analyse im Hintergrund; Status/Ergebnis in Redis."""
    import asyncio  # noqa: F401 — Symmetrie zu create_task-Aufrufer
    import json as _json

    from app.database import SessionLocal
    from app.services_redis import get_redis

    r = get_redis()
    key = f"analyze:{symbol}"
    try:
        async with SessionLocal() as db:
            await yahoo.sync_ohlcv(db, symbol)
            if await yahoo.latest_close(db, symbol) is None:
                payload = {"state": "error",
                           "detail": f"Keine Kursdaten für {symbol} — Symbol korrekt (Yahoo-Notation)?"}
                await r.set(key, _json.dumps(payload), ex=_ANALYZE_STATUS_TTL)
                return
            signal = await run_for_symbol(db, symbol)
        payload = {"state": "done", "created": signal is not None,
                   "signal": _signal_dict(signal) if signal else None}
    except Exception as e:
        logger.exception("Hintergrund-Analyse %s fehlgeschlagen", symbol)
        payload = {"state": "error", "detail": str(e)[:300]}
    await r.set(key, _json.dumps(payload, default=str), ex=_ANALYZE_STATUS_TTL)


@router.post("/signals/run/{symbol}", status_code=202)
async def trigger_analysis(symbol: str, db: AsyncSession = Depends(get_db)):
    """Manuelle Analyse eines Symbols — asynchron und IMMER on demand.

    Mit vielen ungelesenen News dauert die LLM-Analyse Minuten; ein
    synchroner Request liefe in den Proxy-Timeout (der Browser sähe einen
    Internal Server Error, obwohl das Backend sauber fertig rechnet).
    Daher: 202 + Status-Polling über GET .../status.

    Bewusst ohne Watchlist-Zwang: Jedes auflösbare Symbol darf ad-hoc
    analysiert werden (Kosten trägt der eine manuelle Klick). Nur die
    periodische Pipeline bleibt auf die effektive Watchlist beschränkt."""
    import asyncio
    import json as _json

    from app.services_redis import get_redis

    symbol = symbol.upper()
    if await db.get(Asset, symbol) is None:
        try:
            await yahoo.ensure_asset(db, symbol)
        except Exception as e:
            raise HTTPException(status_code=404, detail=f"Symbol {symbol} nicht auflösbar: {e}")
    r = get_redis()
    key = f"analyze:{symbol}"
    existing = await r.get(key)
    if existing and _json.loads(existing).get("state") == "running":
        return {"state": "running", "info": "Analyse läuft bereits"}
    await r.set(key, _json.dumps({"state": "running"}), ex=_ANALYZE_STATUS_TTL)
    asyncio.create_task(_run_analysis_bg(symbol))
    return {"state": "running"}


@router.get("/signals/run/{symbol}/status")
async def analysis_status(symbol: str):
    import json as _json

    from app.services_redis import get_redis

    raw = await get_redis().get(f"analyze:{symbol.upper()}")
    if not raw:
        return {"state": "unknown"}
    return _json.loads(raw)


# ---------------------------------------------------------------- Assets / Charts

@router.get("/assets/{symbol}/chart")
async def asset_chart(symbol: str, days: int = 365, db: AsyncSession = Depends(get_db)):
    from app.config import get_settings

    symbol = symbol.upper()
    # Deckel = Retention-Fenster: mehr Daten kann es nicht geben, und wer
    # die Retention erhöht, bekommt über die Zeit automatisch längere Charts.
    df = await yahoo.load_ohlcv_df(db, symbol, days=min(days, get_settings().retention_ohlcv_days))
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


@router.get("/assets/{symbol}/events")
async def asset_events(symbol: str, db: AsyncSession = Depends(get_db)):
    """Anstehende Termine: Quartalszahlen/Dividende (Yahoo), Biotech-
    Katalysatoren (CatalystAlert, beide Redis-gecacht) + eigene Termine."""
    from app.sources.catalyst import fetch_catalysts

    symbol = symbol.upper()
    out = {"earnings_dates": [], "ex_dividend_date": None, "dividend_date": None,
           "catalysts": [], "custom": []}
    try:
        out.update(await yahoo.fetch_events(symbol))
    except Exception as e:
        logger.warning("Termine für %s nicht abrufbar: %s", symbol, e)
    try:
        out["catalysts"] = await fetch_catalysts(symbol)
    except Exception as e:
        logger.warning("Katalysatoren für %s nicht abrufbar: %s", symbol, e)
    result = await db.execute(
        select(CustomEvent).where(CustomEvent.symbol == symbol).order_by(CustomEvent.date)
    )
    out["custom"] = [{
        "id": str(e.id), "date": e.date, "title": e.title,
        "importance": e.importance, "url": e.url,
    } for e in result.scalars().all()]
    return out


class CustomEventCreate(BaseModel):
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    title: str = Field(min_length=1, max_length=200)
    importance: int = Field(default=7, ge=1, le=10)
    url: str | None = None


@router.post("/assets/{symbol}/events", status_code=201)
async def add_custom_event(symbol: str, payload: CustomEventCreate,
                           db: AsyncSession = Depends(get_db)):
    event = CustomEvent(symbol=symbol.upper(), **payload.model_dump())
    db.add(event)
    await db.commit()
    return {"id": str(event.id), "ok": True}


@router.delete("/events/{event_id}")
async def delete_custom_event(event_id: str, db: AsyncSession = Depends(get_db)):
    import uuid as _uuid
    event = await db.get(CustomEvent, _uuid.UUID(event_id))
    if event:
        await db.delete(event)
        await db.commit()
    return {"ok": True}


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

@router.get("/search")
async def symbol_search(q: str):
    """Symbolsuche nach Klarnamen (Yahoo-Suche, 1h Redis-Cache).

    „Celsius Holdings" → CELH — für Watchlist-/Positions-Eingaben."""
    import json as _json

    import httpx

    from app.services_redis import get_redis

    q = q.strip()
    if len(q) < 2:
        return []
    r = get_redis()
    cache_key = f"search:{q.lower()}"
    cached = await r.get(cache_key)
    if cached is not None:
        return _json.loads(cached)

    out: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=10.0, headers={
            "User-Agent": "Mozilla/5.0 (stx-swing-analyzer)",
        }) as client:
            resp = await client.get(
                "https://query2.finance.yahoo.com/v1/finance/search",
                params={"q": q, "quotesCount": 8, "newsCount": 0, "listsCount": 0},
            )
            resp.raise_for_status()
            data = resp.json()
        for item in data.get("quotes", []):
            if not item.get("symbol"):
                continue
            out.append({
                "symbol": item["symbol"],
                "name": item.get("shortname") or item.get("longname"),
                "exchange": item.get("exchDisp"),
                "type": item.get("quoteType"),
            })
    except Exception as e:
        logger.warning("Symbolsuche %r fehlgeschlagen: %s", q, e)
    await r.set(cache_key, _json.dumps(out), ex=3600)
    return out


@router.get("/dashboard")
async def dashboard(db: AsyncSession = Depends(get_db)):
    from app.analysis.watch_scope import effective_symbols

    # Nur explizit beobachtete Werte im Dashboard — Ad-hoc-Analysen
    # (immer-on-demand) sollen die Übersicht nicht fluten
    scope = await effective_symbols(db)
    signals = await db.execute(
        select(Signal).where(Signal.symbol.in_(scope)).order_by(desc(Signal.ts)).limit(20)
    )
    watch_count = len(scope)
    news_24h = await db.scalar(
        select(func.count()).select_from(NewsArticle)
        .where(NewsArticle.published_at >= datetime.now(timezone.utc) - timedelta(hours=24))
    )
    return {
        "watchlist_count": watch_count,
        "news_last_24h": news_24h,
        "recent_signals": [_signal_dict(s) for s in signals.scalars().all()],
    }
