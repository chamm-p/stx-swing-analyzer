"""Jahresend-Wechselkurse nach CHF für den Steuerreport.

Primär die SNB (offizielle Tageskurse, Jahresende), Fallback Yahoo
(FX-Paar). Beides sind Marktkurse — die amtliche ESTV-Kursliste kann
minimal abweichen; für die Deklaration in aller Regel unkritisch. Der
Report weist die verwendete Quelle aus.

Ergebnis je (Jahr, Währung): Einheiten CHF pro 1 Fremdwährung, 12h in
Redis gecacht.
"""

import csv
import io
import logging
from datetime import datetime, timezone

import httpx

from app.services_redis import get_redis

logger = logging.getLogger(__name__)

_CACHE_TTL = 12 * 3600
# SNB-Tageskurs-Cube (devkua): Fremdwährung → CHF, "per 1 unit"-Reihen
_SNB_URL = "https://data.snb.ch/api/cube/devkua/data/csv/en"


async def _snb_year_end(year: int, currency: str) -> float | None:
    """Letzter SNB-Tageskurs des Jahres für <currency>1 (CHF je 1 Einheit)."""
    params = {"fromDate": f"{year}-12-01", "toDate": f"{year}-12-31",
              "dimSel": f"D0({currency}1)"}
    async with httpx.AsyncClient(timeout=20, headers={
        "User-Agent": "stx-swing-analyzer/1.0"}) as client:
        resp = await client.get(_SNB_URL, params=params)
        resp.raise_for_status()
        text = resp.text
    # CSV mit Metadaten-Vorspann; die Datenzeilen beginnen ab "Date;..."
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.lower().startswith("date")), None)
    if start is None:
        return None
    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])), delimiter=";")
    rows = [r for r in reader if (r.get("Value") or "").strip()]
    if not rows:
        return None
    last = sorted(rows, key=lambda r: r.get("Date", ""))[-1]
    try:
        return float(last["Value"])
    except (ValueError, KeyError):
        return None


async def _yahoo_year_end(year: int, currency: str) -> float | None:
    import asyncio

    import yfinance as yf

    pair = f"{currency}CHF=X"

    def _fetch() -> float | None:
        df = yf.Ticker(pair).history(start=f"{year}-12-15", end=f"{year+1}-01-05",
                                     interval="1d", auto_adjust=True)
        if df is None or df.empty:
            return None
        return float(df["Close"].iloc[-1])

    return await asyncio.to_thread(_fetch)


async def year_end_rate(year: int, currency: str) -> dict:
    """CHF pro 1 <currency> per Jahresende. {rate, source} oder rate=None."""
    currency = (currency or "CHF").upper()
    if currency == "CHF":
        return {"rate": 1.0, "source": "CHF"}

    r = get_redis()
    key = f"fx:{year}:{currency}"
    import json
    cached = await r.get(key)
    if cached:
        return json.loads(cached)

    result: dict = {"rate": None, "source": None}
    try:
        rate = await _snb_year_end(year, currency)
        if rate:
            result = {"rate": round(rate, 6), "source": "SNB"}
    except Exception as e:
        logger.info("SNB-Kurs %s %d nicht verfügbar: %s", currency, year, e)
    if result["rate"] is None:
        try:
            rate = await _yahoo_year_end(year, currency)
            if rate:
                result = {"rate": round(rate, 6), "source": "Yahoo (Marktkurs)"}
        except Exception as e:
            logger.warning("Yahoo-FX %s %d fehlgeschlagen: %s", currency, year, e)

    if result["rate"] is not None:
        await r.set(key, json.dumps(result), ex=_CACHE_TTL)
    return result
