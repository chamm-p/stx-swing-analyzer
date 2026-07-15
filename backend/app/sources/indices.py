"""Index-Mitgliedschaften (S&P 500, Nasdaq 100, DAX/MDAX/SDAX, Euro Stoxx 50)
— hält die Universum-Segmente aktuell.

Quellen je nach Verfügbarkeit: Wikipedia-Tabellen mit Ticker-Spalte
(en oder de) und Wikipedia-Namen + Yahoo-Symbolsuche (SDAX — dort
pflegt Wikipedia keine Ticker).

Scraping ist naturgemäß fragil: jeder Index wird fail-soft geladen und nur
angewendet, wenn eine plausible Mindestanzahl Mitglieder gefunden wurde —
ein kaputter Parser darf kein Segment leerräumen.
"""

import asyncio
import io
import logging
import re

import httpx
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UniverseSymbol

logger = logging.getLogger(__name__)

_UA = {"User-Agent": "Mozilla/5.0 (stx-swing-analyzer; self-hosted screener)"}


_TICKER_COL = re.compile(r"ticker|symbol", re.IGNORECASE)
_NAME_COL = re.compile(r"company|security|name|constituent", re.IGNORECASE)


async def _get(url: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30, headers=_UA, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp


def _normalize(raw: str, style: str) -> str | None:
    """Roh-Ticker → Yahoo-Notation. style: 'us' | 'keep' | Suffix ('.DE')."""
    t = re.sub(r"\[.*?\]", "", str(raw)).strip().upper()
    if not t or len(t) > 15 or " " in t:
        return None
    if style == "us":
        return t.replace(".", "-")  # BRK.B → BRK-B
    if style == "keep":
        return t  # bereits mit Börsen-Suffix (ASML.AS, MC.PA, …)
    return t.split(".")[0] + style  # DE-Indizes: Mnemonic + .DE


def _flat_columns(table: pd.DataFrame) -> list[str]:
    return [" ".join(str(p) for p in c) if isinstance(c, tuple) else str(c)
            for c in table.columns]


def _extract(html: str, style: str) -> dict[str, str]:
    """Beste Tabelle mit Ticker-Spalte finden → {symbol: name}.

    Einzelne Tabellen (z.B. „Änderungen"-Historien mit MultiIndex-Headern)
    dürfen scheitern, ohne die Mitgliederliste zu verhindern."""
    best: dict[str, str] = {}
    for table in pd.read_html(io.StringIO(html)):
        try:
            table = table.copy()
            table.columns = _flat_columns(table)
            cols = list(table.columns)
            tick = next((c for c in cols if _TICKER_COL.search(c)), None)
            name = next((c for c in cols if c != tick and _NAME_COL.search(c)), None)
            if tick is None:
                continue
            members: dict[str, str] = {}
            for _, row in table.iterrows():
                sym = _normalize(row[tick], style)
                if sym:
                    label = row[name] if name is not None else None
                    members[sym] = (str(label).strip()
                                    if label is not None and not pd.isna(label) else sym)
            if len(members) > len(best):
                best = members
        except Exception as e:
            logger.debug("Index-Tabelle übersprungen: %s", e)
    return best


def _extract_names(html: str) -> list[str]:
    """Größte Tabelle mit Namens-Spalte (ohne Ticker) → Firmennamen."""
    best: list[str] = []
    for table in pd.read_html(io.StringIO(html)):
        try:
            table = table.copy()
            table.columns = _flat_columns(table)
            name = next((c for c in table.columns if _NAME_COL.search(c)), None)
            if name is None:
                continue
            values = [str(v).strip() for v in table[name].tolist()
                      if v is not None and not pd.isna(v) and 2 < len(str(v)) < 100]
            if len(values) > len(best):
                best = values
        except Exception as e:
            logger.debug("Namens-Tabelle übersprungen: %s", e)
    return best


async def fetch_wiki_tickers(url: str, style: str) -> dict[str, str]:
    return _extract((await _get(url)).text, style)


_LEGAL_SUFFIX = re.compile(
    r"\s+(SE|AG|KGaA|GmbH|& Co\.?( KGaA| KG)?|Holding(s)?|Group|N\.?V\.?|S\.?A\.?|"
    r"Aktiengesellschaft|Vz\.?|St\.?)\s*$", re.IGNORECASE)


async def fetch_wiki_names_resolved(url: str, suffix: str) -> dict[str, str]:
    """Firmennamen aus Wikipedia + Yahoo-Symbolsuche (für Indizes ohne
    Ticker-Spalte, z.B. SDAX). Nur Treffer mit passendem Börsen-Suffix.
    Rechtsform-Suffixe (SE, AG, KGaA …) werden als Fallback abgestreift —
    Yahoo findet „Deutsche Pfandbriefbank", aber nicht immer „… AG"."""
    names = _extract_names((await _get(url)).text)
    members: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=10, headers=_UA) as client:
        for name in names:
            variants = [name]
            stripped = name
            while _LEGAL_SUFFIX.search(stripped):
                stripped = _LEGAL_SUFFIX.sub("", stripped).strip()
            if stripped and stripped != name:
                variants.append(stripped)
            sym = None
            for query in variants:
                try:
                    resp = await client.get(
                        "https://query2.finance.yahoo.com/v1/finance/search",
                        params={"q": query, "quotesCount": 5,
                                "newsCount": 0, "listsCount": 0},
                    )
                    resp.raise_for_status()
                    quotes = resp.json().get("quotes", [])
                except Exception as e:
                    logger.debug("Symbolsuche %r fehlgeschlagen: %s", query, e)
                    continue
                sym = next((q["symbol"] for q in quotes
                            if q.get("symbol", "").endswith(suffix)
                            and q.get("quoteType") == "EQUITY"), None)
                if sym:
                    break
                await asyncio.sleep(0.25)  # Yahoo-Suche nicht fluten
            if sym:
                members[sym.upper()] = name
    return members


# Reihenfolge = Priorität bei Überschneidungen (AAPL ist in S&P 500 UND
# Nasdaq 100 → wird NASDAQ100; SAP ist in DAX UND Euro Stoxx → bleibt DAX).
# Die API filtert mit "+"-Gruppen (z.B. US+NASDAQ100 = alle US-Aktien).
INDEX_SPECS: list[tuple[str, object, int]] = [
    # en-Wikipedia pflegt für den Nasdaq 100 keine parsebare Tabelle mehr —
    # die deutsche Seite schon (100 Zeilen mit Symbol-Spalte)
    ("NASDAQ100", lambda: fetch_wiki_tickers(
        "https://de.wikipedia.org/wiki/Nasdaq-100", "us"), 80),
    ("US", lambda: fetch_wiki_tickers(
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "us"), 400),
    ("DAX", lambda: fetch_wiki_tickers(
        "https://en.wikipedia.org/wiki/DAX", ".DE"), 30),
    ("MDAX", lambda: fetch_wiki_tickers(
        "https://en.wikipedia.org/wiki/MDAX", ".DE"), 35),
    ("SDAX", lambda: fetch_wiki_names_resolved(
        "https://de.wikipedia.org/wiki/SDAX", ".DE"), 45),
    ("EUROSTOXX", lambda: fetch_wiki_tickers(
        "https://en.wikipedia.org/wiki/EURO_STOXX_50", "keep"), 40),
]
MANAGED_SEGMENTS = {seg for seg, *_ in INDEX_SPECS}


async def refresh_indices(db: AsyncSession) -> dict:
    """Alle Index-Segmente aktualisieren. Liefert Zähler + Fehler je Index.

    Symbole mit Segment außerhalb der verwalteten Indizes (CRYPTO, custom)
    bleiben unangetastet — nur Index-Segmente werden synchronisiert."""
    assigned: dict[str, tuple[str, str]] = {}  # symbol -> (name, segment)
    report: dict[str, dict] = {}
    ok_segments: set[str] = set()

    for segment, fetcher, min_expected in INDEX_SPECS:
        try:
            members = await fetcher()
        except Exception as e:
            logger.warning("Index-Fetch %s fehlgeschlagen: %s", segment, e)
            report[segment] = {"error": str(e)}
            continue
        if len(members) < min_expected:
            logger.warning("Index %s: nur %d Mitglieder gefunden (< %d) — "
                           "Segment bleibt unverändert", segment, len(members), min_expected)
            report[segment] = {"error": f"nur {len(members)} Mitglieder gefunden "
                                        f"(erwartet ≥ {min_expected}) — übersprungen"}
            continue
        ok_segments.add(segment)
        report[segment] = {"members": len(members)}
        for sym, name in members.items():
            assigned.setdefault(sym, (name, segment))

    result = await db.execute(select(UniverseSymbol))
    existing = {u.symbol: u for u in result.scalars().all()}
    added = updated = removed = 0

    for sym, (name, segment) in assigned.items():
        row = existing.get(sym)
        if row is None:
            db.add(UniverseSymbol(symbol=sym, name=name, segment=segment))
            added += 1
        elif row.segment in MANAGED_SEGMENTS and (row.segment != segment or row.name != name):
            row.segment, row.name = segment, name
            updated += 1
        # CRYPTO/custom-Einträge nicht umlabeln

    # Ausgeschiedene Mitglieder entfernen — nur in erfolgreich geladenen
    # Segmenten, sonst würde ein Wikipedia-Ausfall das Segment leeren.
    for sym, row in existing.items():
        if row.segment in ok_segments and sym not in assigned:
            await db.delete(row)
            removed += 1

    await db.commit()
    logger.info("Index-Refresh: %d neu, %d aktualisiert, %d entfernt", added, updated, removed)
    return {"added": added, "updated": updated, "removed": removed, "indices": report}
