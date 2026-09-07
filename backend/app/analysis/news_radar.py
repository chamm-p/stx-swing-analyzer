"""News-Radar: nachrichten-getriebene Entdeckung noch nicht getrackter Werte.

Der Flow (genau die User-Idee): starke, ungetaggte News → LLM extrahiert
das betroffene Unternehmen/Ticker + bewertet die Marktrelevanz → Chart
laden → technische Analyse-Pipeline → melden oder verwerfen.

Schließt die Lücke, dass ein Wert AUSSERHALB des Universums (z.B.
AstraZeneca bei einer FDA-Zulassung) sonst unsichtbar bleibt. Ergebnisse
landen NICHT automatisch in Watchlist/Dashboard — sie werden als
Vorschläge gemeldet und im „📡 News-Radar" angezeigt; Aufnehmen bleibt
manuell (Bias-Schutz).
"""

import json
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import NewsArticle, UniverseSymbol, WatchlistItem, utcnow

logger = logging.getLogger(__name__)

_SEEN_TTL = 7 * 24 * 3600
_HITS_KEY = "news_radar:hits"
_HITS_TTL = 48 * 3600


async def _tracked_symbols(db: AsyncSession) -> set[str]:
    uni = await db.execute(select(UniverseSymbol.symbol))
    wl = await db.execute(select(WatchlistItem.symbol))
    return {r[0] for r in uni.all()} | {r[0] for r in wl.all()}


async def _candidate_articles(db: AsyncSession, hours: int, limit: int) -> list[NewsArticle]:
    """Aktuelle, KEINEM getrackten Symbol zugeordnete Artikel (breite Feeds)."""
    since = utcnow() - timedelta(hours=hours)
    result = await db.execute(
        select(NewsArticle)
        .where(NewsArticle.symbols.is_(None), NewsArticle.published_at >= since)
        .order_by(NewsArticle.published_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


_EXTRACT_SYSTEM = (
    "Du bist ein Finanz-News-Analyst. Zu einer Liste von Schlagzeilen "
    "identifizierst du, welche von einem KONKRETEN börsennotierten Unternehmen "
    "mit einem MARKTBEWEGENDEN Ereignis handeln (z.B. Zulassung, Übernahme, "
    "Gewinnwarnung/-überraschung, Großauftrag, Skandal, Forschungsdurchbruch). "
    "Makro-/Marktnews ohne einzelnen Wert ignorierst du. Antworte NUR mit JSON."
)


def _extract_user(articles: list[NewsArticle]) -> str:
    items = [{"i": i, "headline": a.title, "summary": (a.summary or "")[:200]}
             for i, a in enumerate(articles)]
    return (
        "Schlagzeilen:\n" + json.dumps(items, ensure_ascii=False) + "\n\n"
        "Gib JSON: {\"candidates\": [{\"i\": <index>, \"ticker\": \"<Börsen-Ticker, "
        "Yahoo-Notation wie AAPL, SAP.DE, AZN>\", \"company\": \"<Name>\", "
        "\"significance\": <0..1>, \"event\": \"<kurz>\"}]}. Nur Einträge mit "
        "klarem Einzelwert und significance >= 0.5."
    )


async def scan_news_radar(db: AsyncSession) -> dict:
    from app.alerts.dispatcher import send_email_sync, send_telegram
    from app.analysis.pipeline import run_for_symbol
    from app.llm.client import LLMClient
    from app.services_redis import get_redis
    from app.services_settings import load_settings
    from app.sources import rss, yahoo

    s = get_settings()
    if not s.news_radar_enabled:
        return {"scanned": 0, "hits": 0}

    articles = await _candidate_articles(db, s.news_radar_hours, s.news_radar_scan_limit)
    if not articles:
        return {"scanned": 0, "hits": 0}

    llm = await LLMClient.create(db)
    try:
        data = await llm.complete_json(_EXTRACT_SYSTEM, _extract_user(articles))
    except Exception as e:
        logger.warning("News-Radar-Extraktion fehlgeschlagen: %s", e)
        return {"scanned": len(articles), "hits": 0, "error": str(e)}

    candidates = data.get("candidates") or []
    tracked = await _tracked_symbols(db)
    r = get_redis()
    hits: list[dict] = []

    # stärkste zuerst, gedeckelt
    candidates = sorted(candidates, key=lambda c: c.get("significance") or 0, reverse=True)
    processed = 0
    for c in candidates:
        if processed >= s.news_radar_max_candidates:
            break
        ticker = str(c.get("ticker") or "").strip().upper()
        sig_score = float(c.get("significance") or 0)
        if not ticker or sig_score < s.news_radar_min_significance:
            continue
        if ticker in tracked:
            continue  # schon auf dem Radar
        if await r.get(f"news_radar:seen:{ticker}"):
            continue  # kürzlich schon geprüft
        await r.set(f"news_radar:seen:{ticker}", "1", ex=_SEEN_TTL)
        processed += 1

        # Chart laden — Halluzinationen/nicht handelbare Ticker fallen hier raus
        try:
            await yahoo.ensure_asset(db, ticker)
            await yahoo.sync_ohlcv(db, ticker)
        except Exception as e:
            logger.info("News-Radar: %s nicht auflösbar (%s) — verworfen", ticker, e)
            continue
        if await yahoo.latest_close(db, ticker) is None:
            continue

        # Auslösenden Artikel dem Ticker zuordnen + Symbol-News nachladen,
        # damit die Analyse eine News-Basis hat
        idx = c.get("i")
        if isinstance(idx, int) and 0 <= idx < len(articles):
            art = articles[idx]
            art.symbols = (art.symbols or []) + [ticker]
        await db.commit()
        try:
            await rss.fetch_symbol_news(db)  # holt u.a. den neuen Ticker
        except Exception:
            pass

        # Technische + News-Analyse
        signal = await run_for_symbol(db, ticker)
        if signal is None or signal.action not in ("BUY", "SELL"):
            logger.info("News-Radar: %s (%s) analysiert → %s, verworfen",
                        ticker, c.get("event"), signal.action if signal else "kein Signal")
            continue
        if signal.confidence < s.news_radar_min_confidence:
            logger.info("News-Radar: %s → %s (%.0f%%) unter Schwelle, verworfen",
                        ticker, signal.action, signal.confidence * 100)
            continue

        hits.append({
            "symbol": ticker, "company": c.get("company") or ticker,
            "event": c.get("event"), "headline": articles[idx].title if isinstance(idx, int) else None,
            "action": signal.action, "confidence": round(signal.confidence, 2),
            "target": signal.target_price, "stop": signal.stop_price,
            "ts": utcnow().isoformat(),
        })

    if hits:
        await r.set(_HITS_KEY, json.dumps(hits), ex=_HITS_TTL)
        await _notify(db, hits, load_settings, send_telegram, send_email_sync)
    logger.info("News-Radar: %d Artikel, %d geprüft, %d Treffer",
                len(articles), processed, len(hits))
    return {"scanned": len(articles), "checked": processed, "hits": len(hits)}


async def _notify(db, hits, load_settings, send_telegram, send_email_sync) -> None:
    import asyncio

    base = (get_settings().app_base_url or "").rstrip("/")
    lines = ["📡 News-Radar — neue Werte durch starke News:"]
    for h in hits:
        emoji = "🟢" if h["action"] == "BUY" else "🔴"
        link = f"\n   🔗 {base}/asset/{h['symbol']}" if base else ""
        lines.append(f"{emoji} {h['action']} {h['symbol']} ({h['company']}, "
                     f"{round(h['confidence']*100)}%) — {h['event']}{link}")
    text = "\n".join(lines) + "\n\n⚠️ Kein Watchlist-Eintrag — Vorschlag; manuell prüfen."
    comm = await load_settings(db, "comm")
    if comm.get("telegram_bot_token") and comm.get("telegram_chat_id"):
        try:
            await send_telegram(comm, text)
        except Exception as e:
            logger.error("Radar-Telegram fehlgeschlagen: %s", e)
    if comm.get("smtp_host") and comm.get("alert_email_to"):
        try:
            await asyncio.to_thread(send_email_sync, comm, "[stx] News-Radar", text)
        except Exception as e:
            logger.error("Radar-E-Mail fehlgeschlagen: %s", e)


async def latest_hits() -> list[dict]:
    from app.services_redis import get_redis
    raw = await get_redis().get(_HITS_KEY)
    return json.loads(raw) if raw else []
