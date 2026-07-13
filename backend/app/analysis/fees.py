"""Transaktionsgebühren aus Plattform-Staffeln.

Staffel-Lookup: erst währungsspezifische Tiers (z.B. "EUR"), sonst
"default". Ein Tier {"up_to": 500, "fee": 3.0} gilt für Volumen bis
einschließlich up_to; up_to = null/None heißt „alles darüber".
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def compute_fee(fees: dict | None, currency: str | None, volume: float) -> float:
    """Gebühr für ein Transaktionsvolumen. 0 ohne Plattform/Staffel."""
    if not fees or volume <= 0:
        return 0.0
    tiers = fees.get(currency or "") or fees.get("default") or []
    # Sortiert: konkrete Grenzen aufsteigend, null (∞) zuletzt
    ordered = sorted(tiers, key=lambda t: (t.get("up_to") is None, t.get("up_to") or 0))
    for tier in ordered:
        up_to = tier.get("up_to")
        if up_to is None or volume <= float(up_to):
            try:
                return float(tier.get("fee", 0.0))
            except (TypeError, ValueError):
                return 0.0
    return 0.0


async def portfolio_fee(db: AsyncSession, portfolio, currency: str | None,
                        volume: float) -> float:
    """Gebühr gemäß der Plattform des Portfolios (0 ohne Plattform)."""
    from app.models import TradingPlatform

    if portfolio is None or portfolio.platform_id is None:
        return 0.0
    platform = await db.get(TradingPlatform, portfolio.platform_id)
    if platform is None:
        return 0.0
    return compute_fee(platform.fees, currency, volume)


# Swissquote-Staffel (User-Vorgabe): CHF/USD identisch, EUR weicht in
# der ersten Stufe ab (EUR 5 statt 3).
_SWISSQUOTE_DEFAULT = [
    {"up_to": 500, "fee": 3.0},
    {"up_to": 1000, "fee": 5.0},
    {"up_to": 2000, "fee": 10.0},
    {"up_to": 10000, "fee": 29.0},
    {"up_to": 15000, "fee": 49.0},
    {"up_to": 25000, "fee": 79.0},
    {"up_to": 50000, "fee": 129.0},
    {"up_to": None, "fee": 190.0},
]
_SWISSQUOTE_EUR = [{"up_to": 500, "fee": 5.0}] + _SWISSQUOTE_DEFAULT[1:]


async def seed_platforms(db: AsyncSession) -> None:
    """Legt die Swissquote-Staffel an, wenn noch keine Plattform existiert."""
    from sqlalchemy import select

    from app.models import TradingPlatform

    existing = await db.scalar(select(TradingPlatform.id).limit(1))
    if existing is not None:
        return
    db.add(TradingPlatform(name="Swissquote", fees={
        "default": _SWISSQUOTE_DEFAULT,
        "EUR": _SWISSQUOTE_EUR,
    }))
    await db.commit()
    logger.info("Default-Handelsplattform Swissquote angelegt")
