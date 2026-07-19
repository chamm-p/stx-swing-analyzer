"""Finnhub-Connector: kuratierte Unternehmensnews pro Symbol.

Zweite per-Ticker-Quelle neben Yahoo (gegen das Klumpenrisiko im
Sentiment). Finnhubs company-news-Endpoint erwartet reine US-Ticker —
für .DE/.HK/.SW-Werte und Krypto liefert Yahoo weiter allein.
Gratis-Kontingent: 60 Anfragen/Min; API-Key aus den Einstellungen.
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

_URL = "https://finnhub.io/api/v1/company-news"
_LOOKBACK_DAYS = 7


def eligible(symbol: str) -> bool:
    """Finnhub deckt US-Aktien ab — Suffix-Symbole (.DE/.HK/…) und
    Krypto (-USD) bleiben bei Yahoo."""
    s = symbol.upper()
    return "." not in s and not s.endswith("-USD")


async def fetch_company_news(symbol: str, api_key: str) -> list[dict]:
    """Unternehmensnews der letzten Tage als normalisierte Artikel-Dicts."""
    today = datetime.now(timezone.utc).date()
    params = {
        "symbol": symbol.upper(),
        "from": (today - timedelta(days=_LOOKBACK_DAYS)).isoformat(),
        "to": today.isoformat(),
        "token": api_key,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(_URL, params=params)
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, list):
        return []

    out: list[dict] = []
    for item in data[:40]:
        headline = (item.get("headline") or "").strip()
        if not headline:
            continue
        ts = item.get("datetime")
        published = (datetime.fromtimestamp(float(ts), tz=timezone.utc)
                     if ts else datetime.now(timezone.utc))
        out.append({
            "title": headline,
            "url": item.get("url"),
            "summary": (item.get("summary") or "").strip() or None,
            "published": published,
        })
    return out
