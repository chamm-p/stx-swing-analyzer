"""CatalystAlert-Connector: Biotech-/Pharma-Katalysatoren (FDA/PDUFA,
Phase-3-Readouts, AdCom) für getrackte Symbole.

Öffentliche API (catalystalert.io/api/catalysts). Ohne Account ist der
Kalender auf ~7 Tage Vorschau begrenzt — das deckt das akute
Event-Risiko im Signalhorizont ab. Sparsame Nutzung: nur pro
Watchlist-/Portfolio-Symbol, 12h Redis-Cache, fail-soft leer.
"""

import json
import logging

import httpx

from app.models import utcnow

logger = logging.getLogger(__name__)

_BASE = "https://catalystalert.io/api/catalysts"
_CACHE_TTL = 43200  # 12h
_LOOKAHEAD_DAYS = 45  # Server begrenzt anonym ohnehin auf ~7 Tage


async def fetch_catalysts(symbol: str) -> list[dict]:
    """Kommende Katalysatoren für ein Symbol (leer für Nicht-Biotech)."""
    from datetime import timedelta

    from app.services_redis import get_redis

    r = get_redis()
    cache_key = f"catalyst:{symbol}"
    cached = await r.get(cache_key)
    if cached is not None:
        return json.loads(cached)

    today = utcnow().date()
    params = {
        "search": symbol,
        "from": today.isoformat(),
        "to": (today + timedelta(days=_LOOKAHEAD_DAYS)).isoformat(),
    }
    out: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=20.0, headers={
            "User-Agent": "stx-swing-analyzer/1.0 (self-hosted)",
        }) as client:
            resp = await client.get(_BASE, params=params)
            resp.raise_for_status()
            data = resp.json()
        for item in data.get("data", []):
            # search matcht auch Firmennamen/Drug-Namen — Ticker exakt prüfen
            if (item.get("ticker") or "").upper() != symbol.upper():
                continue
            date = (item.get("expectedDate") or "")[:10]
            if not date:
                continue
            out.append({
                "type": item.get("type"),
                "title": item.get("title"),
                "date": date,
                "date_precision": item.get("datePrecision"),
                "importance": item.get("importance"),
                "phase": item.get("phase"),
                "indication": item.get("indication"),
                "source_url": item.get("sourceUrl"),
            })
        out.sort(key=lambda x: x["date"])
    except Exception as e:
        logger.warning("CatalystAlert für %s nicht abrufbar: %s", symbol, e)
    await r.set(cache_key, json.dumps(out), ex=_CACHE_TTL)
    return out
