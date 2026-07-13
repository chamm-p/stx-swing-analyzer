"""MCP-Server — exponiert Signale, Watchlist, Screener und Portfolios für
externe LLM-Lösungen via Streamable HTTP unter ``/api/mcp``.

Muster übernommen aus cura-stro (dort erfolgreich im Einsatz):
FastMCP stateless als Mount, Token-Guard-Middleware in main.py.
``json_response=True``, damit die Antworten sauber durch den
Next.js-Proxy laufen (Backend-Port ist nicht veröffentlicht).
"""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import desc, func, select

from app.database import SessionLocal
from app.models import (
    AnalysisResult, Asset, NewsArticle, Portfolio, Position, ScreenerResult,
    Signal, UniverseSymbol, WatchlistItem,
)

logger = logging.getLogger("uvicorn.error")

mcp = FastMCP(
    "stx-swing-analyzer",
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def _signal(s: Signal) -> dict:
    return {
        "symbol": s.symbol, "ts": s.ts.isoformat(), "action": s.action,
        "confidence": s.confidence, "composite_score": s.composite_score,
        "technical_score": s.technical_score, "sentiment_score": s.sentiment_score,
        "fundamental_score": s.fundamental_score, "rationale": s.rationale,
        "horizon_days": s.horizon_days, "price_at_signal": s.price_at_signal,
        "eval_return_pct": s.eval_return_pct, "eval_hit": s.eval_hit,
    }


@mcp.tool()
async def get_signals(symbol: str | None = None, limit: int = 20) -> dict:
    """Aktuelle Buy/Sell/Hold-Signale der Analyse-Pipeline.

    Args:
        symbol: optional auf ein Symbol filtern (Yahoo-Notation, z.B. AAPL, SAP.DE).
        limit: max. Anzahl (Default 20).

    Signale enthalten Confidence, Teil-Scores (technisch/Sentiment/fundamental),
    Begründung und — falls der Horizont abgelaufen ist — das Review-Ergebnis.
    """
    async with SessionLocal() as db:
        q = select(Signal).order_by(desc(Signal.ts)).limit(min(limit, 100))
        if symbol:
            q = q.where(Signal.symbol == symbol.upper())
        result = await db.execute(q)
        signals = [_signal(s) for s in result.scalars().all()]
        logger.info("MCP get_signals symbol=%s → %d", symbol, len(signals))
        return {"count": len(signals), "signals": signals}


@mcp.tool()
async def get_top_signals(segment: str | None = None, limit: int = 15) -> dict:
    """Bestenliste des Universum-Screeners (rein technisch, ~110 Titel).

    Args:
        segment: US | DAX | CRYPTO | leer für alle.
        limit: max. Anzahl (Default 15).

    Sortiert nach Signalstärke (|Tech-Score|). Unabhängig von Watchlist
    und Portfolios — gedacht als Kandidaten-Suche ohne eigenen Bias.
    """
    async with SessionLocal() as db:
        last_run = await db.scalar(select(func.max(ScreenerResult.run_at)))
        if last_run is None:
            return {"error": "Noch kein Screener-Lauf vorhanden."}
        q = (
            select(ScreenerResult, UniverseSymbol.name, UniverseSymbol.segment)
            .outerjoin(UniverseSymbol, UniverseSymbol.symbol == ScreenerResult.symbol)
            .where(ScreenerResult.run_at == last_run)
            .order_by(desc(func.abs(ScreenerResult.technical_score)))
            .limit(min(limit, 100))
        )
        if segment:
            q = q.where(UniverseSymbol.segment == segment.upper())
        rows = (await db.execute(q)).all()
        logger.info("MCP get_top_signals segment=%s → %d", segment, len(rows))
        return {
            "run_at": last_run.isoformat(),
            "results": [{
                "symbol": r.symbol, "name": name, "segment": seg, "action": r.action,
                "technical_score": r.technical_score, "close": r.close,
                "rsi14": (r.snapshot or {}).get("rsi14"),
            } for r, name, seg in rows],
        }


@mcp.tool()
async def get_watchlist() -> dict:
    """Effektive Watchlist: manuelle Einträge + offene Positionen aus
    Portfolios mit aktivem Beobachten-Schalter, je mit letztem Signal."""
    from app.analysis.watch_scope import derived_symbols

    async with SessionLocal() as db:
        wl = await db.execute(select(WatchlistItem.symbol))
        manual = {row[0] for row in wl.all()}
        derived = await derived_symbols(db)
        out = []
        for symbol in sorted(manual | set(derived.keys())):
            asset = await db.get(Asset, symbol)
            last = await db.scalar(
                select(Signal).where(Signal.symbol == symbol).order_by(desc(Signal.ts)).limit(1)
            )
            out.append({
                "symbol": symbol,
                "name": asset.name if asset else None,
                "source": "watchlist" if symbol in manual else "portfolio",
                "portfolios": derived.get(symbol),
                "last_signal": _signal(last) if last else None,
            })
        logger.info("MCP get_watchlist → %d", len(out))
        return {"count": len(out), "items": out}


@mcp.tool()
async def get_asset_analysis(symbol: str) -> dict:
    """Komplette Analyse-Sicht eines Assets: Indikator-Snapshot, letztes
    LLM-Review, letzte Signale und aktuelle News mit Sentiment.

    Args:
        symbol: Yahoo-Notation, z.B. AAPL, SAP.DE, BTC-USD.
    """
    from app.processing.indicators import compute_indicators
    from app.sources.yahoo import load_ohlcv_df

    symbol = symbol.upper()
    async with SessionLocal() as db:
        asset = await db.get(Asset, symbol)
        df = await load_ohlcv_df(db, symbol)
        snapshot = compute_indicators(df)["snapshot"] if not df.empty else {}

        review = await db.scalar(
            select(AnalysisResult).where(AnalysisResult.symbol == symbol,
                                         AnalysisResult.kind == "asset_review")
            .order_by(desc(AnalysisResult.ts)).limit(1)
        )
        signals = (await db.execute(
            select(Signal).where(Signal.symbol == symbol).order_by(desc(Signal.ts)).limit(5)
        )).scalars().all()
        news = (await db.execute(
            select(NewsArticle).where(NewsArticle.symbols.any(symbol))
            .order_by(desc(NewsArticle.published_at)).limit(10)
        )).scalars().all()

        logger.info("MCP get_asset_analysis %s → %d Kerzen", symbol, len(df))
        return {
            "symbol": symbol,
            "name": asset.name if asset else None,
            "asset_type": asset.asset_type if asset else None,
            "indicators": snapshot,
            "latest_review": review.payload if review else None,
            "signals": [_signal(s) for s in signals],
            "news": [{
                "title": n.title, "published_at": n.published_at.isoformat(),
                "source": n.source_name, "sentiment_score": n.sentiment_score,
                "sentiment_label": n.sentiment_label, "url": n.url,
            } for n in news],
        }


@mcp.tool()
async def get_portfolios() -> dict:
    """Alle Portfolios (echt/trial/auto) mit Positionen, Bewertung und P/L."""
    from app.analysis.portfolio_calc import position_value
    from app.sources.yahoo import latest_close

    async with SessionLocal() as db:
        portfolios = (await db.execute(select(Portfolio).order_by(Portfolio.created_at))).scalars().all()
        out = []
        for pf in portfolios:
            positions = (await db.execute(
                select(Position).where(Position.portfolio_id == pf.id)
            )).scalars().all()
            pos_out = []
            for p in positions:
                current = await latest_close(db, p.symbol) if p.exit_date is None else None
                pos_out.append({
                    "symbol": p.symbol, "quantity": p.quantity,
                    "entry_price": p.entry_price, "entry_date": p.entry_date.isoformat(),
                    "is_open": p.exit_date is None, "source": p.source,
                    **position_value(p, current),
                })
            out.append({
                "id": pf.id, "name": pf.name, "kind": pf.kind,
                "watch_enabled": pf.watch_enabled,
                "cash": pf.cash if pf.kind == "auto" else None,
                "config": pf.config if pf.kind == "auto" else None,
                "positions": pos_out,
            })
        logger.info("MCP get_portfolios → %d", len(out))
        return {"count": len(out), "portfolios": out}


@mcp.tool()
async def get_signal_review() -> dict:
    """Signalqualität: Trefferquote und Ø-Rendite je Aktion/Asset-Klasse
    (alle Signale werden nach Horizont-Ablauf gegen den Kurs ausgewertet)."""
    from app.analysis.signal_review import review_summary

    async with SessionLocal() as db:
        res = await review_summary(db)
        logger.info("MCP get_signal_review → %d ausgewertet", res.get("evaluated_count", 0))
        return res


@mcp.tool()
async def add_to_watchlist(symbol: str) -> dict:
    """Fügt ein Symbol zur Watchlist hinzu (löst Kurs-Sync aus; die volle
    LLM-Analyse folgt im nächsten Pipeline-Lauf).

    Args:
        symbol: Yahoo-Notation, z.B. AAPL, SAP.DE, BTC-USD.
    """
    from app.sources import yahoo

    symbol = symbol.upper().strip()
    async with SessionLocal() as db:
        if await db.get(WatchlistItem, symbol):
            return {"ok": False, "error": f"{symbol} ist bereits auf der Watchlist."}
        try:
            asset = await yahoo.ensure_asset(db, symbol)
        except Exception as e:
            return {"ok": False, "error": f"Symbol {symbol} nicht auflösbar: {e}"}
        db.add(WatchlistItem(symbol=symbol))
        await db.commit()
        try:
            await yahoo.sync_ohlcv(db, symbol)
        except Exception:
            pass
        logger.info("MCP add_to_watchlist %s", symbol)
        return {"ok": True, "symbol": symbol, "name": asset.name}


@mcp.tool()
async def run_analysis(symbol: str) -> dict:
    """Analyse-Pipeline für ein Symbol sofort ausführen (Indikatoren, LLM,
    Scoring). Symbol muss auf der Watchlist oder in einem beobachteten
    Portfolio sein. Kann je nach LLM einige Sekunden dauern.

    Args:
        symbol: Yahoo-Notation.
    """
    from app.analysis.pipeline import run_for_symbol
    from app.analysis.watch_scope import effective_symbols
    from app.sources import yahoo

    symbol = symbol.upper()
    async with SessionLocal() as db:
        if symbol not in await effective_symbols(db):
            return {"ok": False, "error": "Weder auf der Watchlist noch in einem beobachteten Portfolio."}
        await yahoo.sync_ohlcv(db, symbol)
        signal = await run_for_symbol(db, symbol)
        logger.info("MCP run_analysis %s → %s", symbol, signal.action if signal else "kein neues Signal")
        if signal is None:
            return {"ok": True, "created": False,
                    "info": "Kein neues Signal (unverändert innerhalb des Refresh-Fensters)."}
        return {"ok": True, "created": True, "signal": _signal(signal)}
