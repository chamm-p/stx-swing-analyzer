"""Effektive Watchlist = manuelle Einträge ∪ offene Positionen aus
Portfolios mit aktivem „Beobachten"-Schalter (watch_enabled).

Abgeleitete Symbole durchlaufen dieselbe Analyse-Pipeline; für Alerts
gelten Defaults (aktiv, min. Confidence 0.5), solange kein manueller
Watchlist-Eintrag existiert — der hat immer Vorrang.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Portfolio, Position, WatchlistItem

DERIVED_MIN_CONFIDENCE = 0.5


async def derived_symbols(db: AsyncSession) -> dict[str, list[str]]:
    """Symbol → Namen der beobachtenden Portfolios (nur offene Positionen)."""
    result = await db.execute(
        select(Position.symbol, Portfolio.name)
        .join(Portfolio, Portfolio.id == Position.portfolio_id)
        .where(Position.exit_date.is_(None), Portfolio.watch_enabled == True)  # noqa: E712
    )
    out: dict[str, list[str]] = {}
    for symbol, pf_name in result.all():
        names = out.setdefault(symbol, [])
        if pf_name not in names:
            names.append(pf_name)
    return out


async def effective_symbols(db: AsyncSession) -> list[str]:
    """Alle zu analysierenden Symbole (Watchlist ∪ beobachtete Portfolios)."""
    wl = await db.execute(select(WatchlistItem.symbol))
    symbols = {row[0] for row in wl.all()}
    symbols |= set((await derived_symbols(db)).keys())
    return sorted(symbols)


async def alert_config(db: AsyncSession, symbol: str) -> tuple[bool, float]:
    """(alert_enabled, min_confidence) — Watchlist-Eintrag vor Ableitung."""
    item = await db.get(WatchlistItem, symbol)
    if item is not None:
        return item.alert_enabled, item.min_confidence
    derived = await derived_symbols(db)
    if symbol in derived:
        return True, DERIVED_MIN_CONFIDENCE
    return False, 1.0
