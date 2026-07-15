"""RSS-News-Connector: Feeds abrufen, deduplizieren, Symbolen zuordnen."""

import asyncio
import hashlib
import logging
from datetime import datetime, timezone

import feedparser
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Asset, DataSource, NewsArticle, WatchlistItem, utcnow
from app.sources.base import with_retry

# Reddit & Co. blocken generische Bot-UAs — beschreibende Kennung nach
# Reddit-Konvention ("platform:app:version") senkt die 429-Quote deutlich.
_FEED_HEADERS = {
    "User-Agent": "web:stx-swing-analyzer:v1.0 (self-hosted single-user RSS reader)",
    "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
}

logger = logging.getLogger(__name__)

DEFAULT_FEEDS = [
    # International
    ("Yahoo Finance Top", "https://finance.yahoo.com/news/rssindex"),
    ("MarketWatch Top Stories", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("CNBC Finance", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"),
    ("Investing.com News", "https://www.investing.com/rss/news_25.rss"),
    ("Seeking Alpha Market News", "https://seekingalpha.com/market_currents.xml"),
    # Deutschsprachig
    ("Handelsblatt Finanzen", "https://www.handelsblatt.com/contentexport/feed/finanzen"),
    ("n-tv Wirtschaft", "https://www.n-tv.de/wirtschaft/rss"),
    ("tagesschau Wirtschaft", "https://www.tagesschau.de/wirtschaft/index~rss2.xml"),
    # Social (Reddit liefert Atom-Feeds ohne API-Key)
    ("Reddit r/stocks", "https://www.reddit.com/r/stocks/.rss"),
    ("Reddit r/wallstreetbets", "https://www.reddit.com/r/wallstreetbets/.rss"),
]

# Symbolbezogener Yahoo-News-Feed (liefert gezielte Artikel pro Ticker)
SYMBOL_FEED_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"


async def seed_default_sources(db: AsyncSession) -> None:
    """Legt fehlende Default-RSS-Quellen an (idempotent, per URL)."""
    result = await db.execute(select(DataSource.url))
    existing_urls = {row[0] for row in result.all()}
    added = 0
    for name, url in DEFAULT_FEEDS:
        if url not in existing_urls:
            db.add(DataSource(kind="news_rss", name=name, url=url, enabled=True))
            added += 1
    if added:
        await db.commit()
        logger.info("Default-News-Quellen ergänzt (%d neue Feeds)", added)


def _entry_published(entry) -> datetime:
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return utcnow()


def _match_symbols(text: str, keyword_map: dict[str, list[str]]) -> list[str]:
    """Ordnet einen Artikel Watchlist-Symbolen zu (Keyword-Matching, case-insensitiv)."""
    lowered = text.lower()
    hits = []
    for symbol, keywords in keyword_map.items():
        for kw in keywords:
            if kw and len(kw) >= 2 and kw.lower() in lowered:
                hits.append(symbol)
                break
    return hits


async def _build_keyword_map(db: AsyncSession) -> dict[str, list[str]]:
    result = await db.execute(
        select(Asset).join(WatchlistItem, WatchlistItem.symbol == Asset.symbol)
    )
    return {a.symbol: (a.keywords or [a.symbol]) for a in result.scalars().all()}


async def fetch_source(db: AsyncSession, source: DataSource) -> int:
    """Einen RSS-Feed abrufen und neue Artikel speichern. Liefert Anzahl neuer Artikel.

    429-Schutz (Reddit & Co. filtern Auto-Abfragen): beschreibender
    User-Agent nach Reddit-Konvention statt Generic-Bot-Kennung, und bei
    429 eine Redis-Pause (Retry-After, min. 2h) — weiterhämmern
    verlängert die Sperre nur."""
    from app.services_redis import get_redis
    from app.sources.base import RateLimited

    r = get_redis()
    cooldown_key = f"rss:cooldown:{source.id}"
    ttl = await r.ttl(cooldown_key)
    if ttl and ttl > 0:
        source.last_error = f"Rate-Limit-Pause — nächster Versuch in ~{ttl // 60} Min"
        await db.commit()
        return 0

    async def _get() -> bytes:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True,
                                     headers=_FEED_HEADERS) as client:
            resp = await client.get(source.url)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After") or 0)
                raise RateLimited(max(retry_after, 7200))
            resp.raise_for_status()
            return resp.content

    try:
        raw = await with_retry(_get, label=f"rss:{source.name}")
    except RateLimited as e:
        await r.set(cooldown_key, "1", ex=e.wait_seconds)
        source.last_error = (f"429 Too Many Requests — Quelle pausiert "
                             f"{e.wait_seconds // 3600}h (automatisch)")
        source.last_fetch_at = utcnow()
        await db.commit()
        logger.warning("rss:%s rate-limited — Pause %ds", source.name, e.wait_seconds)
        return 0
    except RuntimeError as e:
        source.last_error = str(e)[:500]
        source.last_fetch_at = utcnow()
        await db.commit()
        return 0

    feed = await asyncio.to_thread(feedparser.parse, raw)
    keyword_map = await _build_keyword_map(db)

    new_count = 0
    for entry in feed.entries[:100]:
        url = getattr(entry, "link", None)
        title = getattr(entry, "title", "") or ""
        if not title:
            continue
        url_hash = hashlib.sha256((url or title).encode()).hexdigest()
        exists = await db.scalar(
            select(NewsArticle.id).where(NewsArticle.url_hash == url_hash).limit(1)
        )
        if exists:
            continue
        summary = getattr(entry, "summary", None)
        symbols = _match_symbols(f"{title} {summary or ''}", keyword_map)
        db.add(NewsArticle(
            published_at=_entry_published(entry),
            source_id=source.id,
            source_name=source.name,
            title=title,
            url=url,
            url_hash=url_hash,
            summary=summary,
            symbols=symbols or None,
        ))
        new_count += 1

    source.last_fetch_at = utcnow()
    source.last_error = None
    await db.commit()
    if new_count:
        logger.info("RSS %s: %d neue Artikel", source.name, new_count)
    return new_count


async def _tracked_symbols(db: AsyncSession) -> list[str]:
    """Symbole aus Watchlist + offenen Portfolio-Positionen."""
    from app.models import Position
    wl = await db.execute(select(WatchlistItem.symbol))
    pos = await db.execute(select(Position.symbol).where(Position.exit_date.is_(None)).distinct())
    return sorted({row[0] for row in wl.all()} | {row[0] for row in pos.all()})


async def fetch_symbol_news(db: AsyncSession) -> int:
    """Symbolbezogene Yahoo-Feeds für alle getrackten Symbole abrufen.

    Artikel werden direkt dem Symbol zugeordnet (kein Keyword-Matching
    nötig) — das liefert deutlich gezieltere News als die breiten Feeds.
    """
    total = 0
    for symbol in await _tracked_symbols(db):
        url = SYMBOL_FEED_URL.format(symbol=symbol)

        async def _get() -> bytes:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True,
                                         headers=_FEED_HEADERS) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.content

        try:
            raw = await with_retry(_get, retries=2, label=f"symbol-feed:{symbol}")
        except RuntimeError as e:
            logger.warning("Symbol-Feed %s nicht abrufbar: %s", symbol, e)
            continue

        feed = await asyncio.to_thread(feedparser.parse, raw)
        for entry in feed.entries[:40]:
            title = getattr(entry, "title", "") or ""
            if not title:
                continue
            link = getattr(entry, "link", None)
            url_hash = hashlib.sha256((link or title).encode()).hexdigest()
            existing = await db.scalar(
                select(NewsArticle).where(NewsArticle.url_hash == url_hash).limit(1)
            )
            if existing:
                # Artikel kann über mehrere Wege reinkommen — Symbol ergänzen
                if existing.symbols is None or symbol not in existing.symbols:
                    existing.symbols = (existing.symbols or []) + [symbol]
                continue
            db.add(NewsArticle(
                published_at=_entry_published(entry),
                source_name=f"Yahoo ({symbol})",
                title=title,
                url=link,
                url_hash=url_hash,
                summary=getattr(entry, "summary", None),
                symbols=[symbol],
            ))
            total += 1
        await db.commit()
    if total:
        logger.info("Symbol-Feeds: %d neue Artikel", total)
    return total


async def fetch_all_sources(db: AsyncSession) -> int:
    result = await db.execute(
        select(DataSource).where(DataSource.enabled == True, DataSource.kind == "news_rss")  # noqa: E712
        .order_by(DataSource.priority)
    )
    total = 0
    for source in result.scalars().all():
        total += await fetch_source(db, source)
    return total
