"""Goldene Swing-Regeln: Positionsgröße nach der 1%-Regel + CRV-Guard.

1%-Regel: Ein einzelner Trade darf höchstens ``risk_pct`` Prozent des
Portfoliowerts kosten, wenn er am Stop ausgestoppt wird. Daraus folgt
die Stückzahl: Risikobudget ÷ Abstand zum Stop. Ohne validen Stop gibt
es keinen Risiko-Vorschlag (None) — nie stillschweigend raten.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def risk_based_quantity(portfolio_value: float, price: float,
                        stop_price: float | None, risk_pct: float) -> float | None:
    """Max. Stückzahl nach der Risiko-Regel; None ohne validen Stop."""
    if (not price or price <= 0 or portfolio_value <= 0 or risk_pct <= 0
            or stop_price is None or stop_price <= 0 or stop_price >= price):
        return None
    risk_budget = portfolio_value * risk_pct / 100.0
    return risk_budget / (price - stop_price)


def crv(price: float, target_price: float | None, stop_price: float | None) -> float | None:
    """Chance-Risiko-Verhältnis; None wenn Ziel/Stop fehlen oder unbrauchbar."""
    if (not price or target_price is None or stop_price is None
            or target_price <= price or stop_price >= price or stop_price <= 0):
        return None
    return (target_price - price) / (price - stop_price)


async def portfolio_market_value(db: AsyncSession, portfolio, open_positions) -> float:
    """Aktueller Gesamtwert: Cash + Marktwert der offenen Positionen
    (letzter Close; Positionen ohne Kursdaten zählen mit Einstandswert)."""
    from app.sources.yahoo import latest_close

    total = float(portfolio.cash or 0.0)
    for p in open_positions:
        price = await latest_close(db, p.symbol)
        total += p.quantity * (price if price else p.entry_price)
    return total
