"""Index-Mitgliedschaften (S&P 500, Nasdaq 100, DAX/MDAX/SDAX, Euro Stoxx 50)
aus Wikipedia — hält die Universum-Segmente aktuell.

Scraping ist naturgemäß fragil: jeder Index wird fail-soft geladen und nur
angewendet, wenn eine plausible Mindestanzahl Mitglieder gefunden wurde —
ein kaputter Parser darf kein Segment leerräumen.
"""

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

# Reihenfolge = Priorität bei Überschneidungen (AAPL ist in S&P 500 UND
# Nasdaq 100 → wird NASDAQ100; SAP ist in DAX UND Euro Stoxx → bleibt DAX).
# Die API filtert mit "+"-Gruppen (z.B. US+NASDAQ100 = alle US-Aktien).
INDEX_SPECS = [
    ("NASDAQ100", "https://en.wikipedia.org/wiki/Nasdaq-100", 80, "us"),
    ("US", "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", 400, "us"),
    ("DAX", "https://en.wikipedia.org/wiki/DAX", 30, ".DE"),
    ("MDAX", "https://en.wikipedia.org/wiki/MDAX", 35, ".DE"),
    ("SDAX", "https://en.wikipedia.org/wiki/SDAX", 45, ".DE"),
    ("EUROSTOXX", "https://en.wikipedia.org/wiki/EURO_STOXX_50", 40, "keep"),
]
MANAGED_SEGMENTS = {seg for seg, *_ in INDEX_SPECS}

_TICKER_COL = re.compile(r"ticker|symbol", re.IGNORECASE)
_NAME_COL = re.compile(r"company|security|name|constituent", re.IGNORECASE)


def _normalize(raw: str, style: str) -> str | None:
    """Wikipedia-Ticker → Yahoo-Notation. style: 'us' | 'keep' | Suffix."""
    t = re.sub(r"\[.*?\]", "", str(raw)).strip().upper()
    if not t or len(t) > 15 or " " in t:
        return None
    if style == "us":
        return t.replace(".", "-")  # BRK.B → BRK-B
    if style == "keep":
        return t  # bereits mit Börsen-Suffix (ASML.AS, MC.PA, …)
    return t.split(".")[0] + style  # DE-Indizes: Mnemonic + .DE


def _extract(html: str, style: str) -> dict[str, str]:
    """Beste Tabelle mit Ticker-Spalte finden → {symbol: name}."""
    best: dict[str, str] = {}
    for table in pd.read_html(io.StringIO(html)):
        cols = [str(c) for c in table.columns]
        tick = next((c for c in cols if _TICKER_COL.search(c)), None)
        name = next((c for c in cols if c != tick and _NAME_COL.search(c)), None)
        if tick is None:
            continue
        members: dict[str, str] = {}
        for _, row in table.iterrows():
            sym = _normalize(row[tick], style)
            if sym:
                label = row[name] if name is not None else None
                members[sym] = str(label).strip() if label is not None and not pd.isna(label) else sym
        if len(members) > len(best):
            best = members
    return best


async def fetch_index(url: str, style: str) -> dict[str, str]:
    async with httpx.AsyncClient(timeout=30, headers=_UA, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    return _extract(resp.text, style)


async def refresh_indices(db: AsyncSession) -> dict:
    """Alle Index-Segmente aktualisieren. Liefert Zähler + Fehler je Index.

    Symbole mit Segment außerhalb der verwalteten Indizes (CRYPTO, custom)
    bleiben unangetastet — nur Index-Segmente werden synchronisiert."""
    assigned: dict[str, tuple[str, str]] = {}  # symbol -> (name, segment)
    report: dict[str, dict] = {}
    ok_segments: set[str] = set()

    for segment, url, min_expected, style in INDEX_SPECS:
        try:
            members = await fetch_index(url, style)
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
