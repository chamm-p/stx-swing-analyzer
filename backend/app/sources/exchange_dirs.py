"""Komplette Börsenverzeichnisse für den Discovery-Scan.

US: NASDAQ-Trader-Symboldateien (NASDAQ + NYSE/AMEX, ~7000 Aktien).
DE: XETRA-Instrumentenliste der Deutschen Börse (~1500 Aktien).

Beide Quellen werden 24h in Redis gecacht — die Verzeichnisse ändern sich
selten, und der nächtliche Scan soll nicht an einem Directory-Ausfall
scheitern (fail-soft je Region).
"""

import json
import logging
import re

import httpx

from app.services_redis import get_redis

logger = logging.getLogger(__name__)

_UA = {"User-Agent": "Mozilla/5.0 (stx-swing-analyzer; self-hosted screener)"}
_CACHE_TTL = 86400

NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
XETRA_PAGE = ("https://www.deutsche-boerse-cash-market.com/dbcm-de/instrumente-statistiken/"
              "alle-handelbaren-instrumente/alle-handelbaren-instrumente")

# Keine handelbaren Swing-Kandidaten: Warrants, Rights, Units, Preferreds …
_EXCLUDE_NAME = re.compile(
    r"warrant|right(s)?\b|unit(s)?\b|preferred|preference|%|notes? due|debenture",
    re.IGNORECASE)


async def _get_text(url: str) -> str:
    async with httpx.AsyncClient(timeout=60, headers=_UA, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


async def _cached(key: str, loader) -> list[list[str]]:
    r = get_redis()
    hit = await r.get(key)
    if hit is not None:
        return json.loads(hit)
    rows = await loader()
    await r.set(key, json.dumps(rows), ex=_CACHE_TTL)
    return rows


def _parse_nasdaq_file(text: str, symbol_col: str, is_other: bool) -> list[list[str]]:
    lines = text.strip().splitlines()
    header = lines[0].split("|")
    idx = {name: i for i, name in enumerate(header)}
    out = []
    for line in lines[1:]:
        parts = line.split("|")
        if len(parts) != len(header) or line.startswith("File Creation"):
            continue
        sym = parts[idx[symbol_col]].strip()
        name = parts[idx["Security Name"]].strip()
        if not sym or _EXCLUDE_NAME.search(name):
            continue
        if parts[idx["Test Issue"]].strip() == "Y":
            continue
        if "ETF" in idx and parts[idx["ETF"]].strip() == "Y":
            continue
        if is_other:
            exchange = parts[idx["Exchange"]].strip() if "Exchange" in idx else ""
            if exchange not in ("A", "N"):
                continue  # nur NYSE/AMEX; P (Arca) ist fast nur ETF-Terrain
        # Klassen-Suffixe in Yahoo-Notation (BRK.B → BRK-B); $-Preferred raus
        if "$" in sym:
            continue
        out.append([sym.replace(".", "-"), name])
    return out


async def us_symbols() -> list[list[str]]:
    """[[symbol, name], …] aller US-Common-Stocks (NASDAQ + NYSE/AMEX)."""
    async def load() -> list[list[str]]:
        rows: list[list[str]] = []
        nasdaq = await _get_text(NASDAQ_URL)
        rows += _parse_nasdaq_file(nasdaq, "Symbol", is_other=False)
        other = await _get_text(OTHER_URL)
        rows += _parse_nasdaq_file(other, "ACT Symbol", is_other=True)
        seen: set[str] = set()
        return [r for r in rows if not (r[0] in seen or seen.add(r[0]))]

    return await _cached("discovery:dir:us", load)


async def xetra_symbols() -> list[list[str]]:
    """[[symbol, name], …] aller XETRA-Aktien (Mnemonic + .DE, nur EUR/CS).

    Die CSV-URL wandert (Blob-Hash) — daher wird der Download-Link von der
    Übersichtsseite gescrapt."""
    async def load() -> list[list[str]]:
        page = await _get_text(XETRA_PAGE)
        m = re.search(r'href="([^"]*allTradableInstruments[^"]*\.csv)"', page)
        if not m:
            raise RuntimeError("XETRA-CSV-Link nicht auf der Übersichtsseite gefunden")
        url = m.group(1)
        if url.startswith("/"):
            url = "https://www.deutsche-boerse-cash-market.com" + url
        csv_text = await _get_text(url)

        lines = csv_text.splitlines()
        header_i = next((i for i, l in enumerate(lines)
                         if "Mnemonic" in l and "Instrument" in l), None)
        if header_i is None:
            raise RuntimeError("XETRA-CSV: Header-Zeile nicht gefunden")
        header = [h.strip() for h in lines[header_i].split(";")]
        idx = {name: i for i, name in enumerate(header)}
        type_col = next((c for c in header if "Instrument Type" in c or c == "Security Type"), None)
        cur_col = next((c for c in header if "Currency" in c), None)
        name_col = next((c for c in header if c in ("Instrument", "Instrument Name")), "Instrument")

        rows: list[list[str]] = []
        seen: set[str] = set()
        for line in lines[header_i + 1:]:
            parts = line.split(";")
            if len(parts) < len(header):
                continue
            mnemonic = parts[idx.get("Mnemonic", 0)].strip()
            if not mnemonic or not re.fullmatch(r"[A-Z0-9]{2,6}", mnemonic):
                continue
            if type_col and parts[idx[type_col]].strip() not in ("CS", "Common stock", "Aktie"):
                continue
            if cur_col and parts[idx[cur_col]].strip() not in ("EUR", ""):
                continue
            name = parts[idx.get(name_col, 1)].strip()
            if _EXCLUDE_NAME.search(name):
                continue
            sym = mnemonic + ".DE"
            if sym not in seen:
                seen.add(sym)
                rows.append([sym, name])
        if len(rows) < 300:
            raise RuntimeError(f"XETRA-CSV: nur {len(rows)} Aktien geparst — Format geändert?")
        return rows

    return await _cached("discovery:dir:de", load)
