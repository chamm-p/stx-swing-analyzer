"""Jahresend-Wechselkurse nach CHF für den Steuerreport.

Für die Vermögensbewertung per 31.12. gilt der Jahresend-KURS (Stichtag),
nicht der Jahresdurchschnitt. Wir nehmen daher den Marktkurs des letzten
Handelstags des Jahres (Yahoo-FX-Paar) — er deckt sich eng mit dem
amtlichen ESTV-Jahresendkurs (im Test USD 2024: 0.912 vs. ESTV ~0.904;
der SNB-Jahresdurchschnitt läge mit 0.880 rund 3% daneben und wäre für
den Stichtag das falsche Maß).

Ergebnis je (Jahr, Währung): CHF pro 1 Fremdwährung, 12h Redis-Cache.
"""

import asyncio
import json
import logging

import yfinance as yf

from app.services_redis import get_redis

logger = logging.getLogger(__name__)

_CACHE_TTL = 12 * 3600


def _yahoo_year_end_sync(year: int, currency: str) -> float | None:
    pair = f"{currency}CHF=X"
    df = yf.Ticker(pair).history(start=f"{year}-12-15", end=f"{year+1}-01-05",
                                 interval="1d", auto_adjust=True)
    if df is None or df.empty:
        return None
    return float(df["Close"].iloc[-1])


async def year_end_rate(year: int, currency: str) -> dict:
    """CHF pro 1 <currency> zum Jahresende (Stichtagskurs 31.12.).
    {rate, source}; rate=None wenn nicht ermittelbar."""
    currency = (currency or "CHF").upper()
    if currency == "CHF":
        return {"rate": 1.0, "source": "CHF"}

    r = get_redis()
    key = f"fx:{year}:{currency}"
    cached = await r.get(key)
    if cached:
        return json.loads(cached)

    result: dict = {"rate": None, "source": None}
    try:
        rate = await asyncio.to_thread(_yahoo_year_end_sync, year, currency)
        if rate:
            result = {"rate": round(rate, 6), "source": "Jahresend-Marktkurs (31.12.)"}
    except Exception as e:
        logger.warning("FX %s %d fehlgeschlagen: %s", currency, year, e)

    if result["rate"] is not None:
        await r.set(key, json.dumps(result), ex=_CACHE_TTL)
    return result
