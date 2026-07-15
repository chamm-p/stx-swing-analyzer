"""Transaktionsgebühren aus Plattform-Staffeln.

Staffel-Lookup: erst währungsspezifische Tiers (z.B. "EUR"), sonst
"default". Ein Tier gilt für Volumen bis einschließlich up_to
(null/None = „alles darüber") und kennt drei Modelle:

    {"up_to": 500, "fee": 3.0}                     Flat (Swissquote-Stil)
    {"pct": 0.0005, "min": 1.25, "max": 29.0}      Prozent vom Volumen (IBKR EU)
    {"per_share": 0.005, "min": 1.0, "max_pct": 0.01}  pro Aktie (IBKR US)

min/max/max_pct sind optional und für alle Modelle anwendbar
(max_pct = Deckel als Anteil vom Volumen).
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _tier_fee(tier: dict, volume: float, quantity: float | None) -> float:
    try:
        if tier.get("pct") is not None:
            base = float(tier["pct"]) * volume
        elif tier.get("per_share") is not None:
            base = float(tier["per_share"]) * float(quantity or 0)
        else:
            base = float(tier.get("fee", 0.0))
        if tier.get("min") is not None:
            base = max(base, float(tier["min"]))
        if tier.get("max") is not None:
            base = min(base, float(tier["max"]))
        if tier.get("max_pct") is not None:
            base = min(base, float(tier["max_pct"]) * volume)
        return round(max(base, 0.0), 2)
    except (TypeError, ValueError):
        return 0.0


def compute_fee(fees: dict | None, currency: str | None, volume: float,
                quantity: float | None = None) -> float:
    """Gebühr für ein Transaktionsvolumen. 0 ohne Plattform/Staffel.

    quantity (Stückzahl) wird nur für per-Share-Modelle gebraucht —
    fehlt sie dort, greift das Tier-Minimum."""
    if not fees or volume <= 0:
        return 0.0
    tiers = fees.get(currency or "") or fees.get("default") or []
    # Sortiert: konkrete Grenzen aufsteigend, null (∞) zuletzt
    ordered = sorted(tiers, key=lambda t: (t.get("up_to") is None, t.get("up_to") or 0))
    for tier in ordered:
        up_to = tier.get("up_to")
        if up_to is None or volume <= float(up_to):
            return _tier_fee(tier, volume, quantity)
    return 0.0


async def portfolio_fee(db: AsyncSession, portfolio, currency: str | None,
                        volume: float, quantity: float | None = None) -> float:
    """Gebühr gemäß der Plattform des Portfolios (0 ohne Plattform)."""
    from app.models import TradingPlatform

    if portfolio is None or portfolio.platform_id is None:
        return 0.0
    platform = await db.get(TradingPlatform, portfolio.platform_id)
    if platform is None:
        return 0.0
    return compute_fee(platform.fees, currency, volume, quantity)


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


# IBKR Pro (Stand Juli 2026, Quelle interactivebrokers.com — Sätze im UI
# prüfbar/anpassbar). Tiered enthält zusätzlich variable Börsen-/
# Regulierungsgebühren, die hier bewusst NICHT modelliert sind — die
# echte Tiered-Gebühr liegt daher etwas höher.
_IBKR_FIXED = {
    "USD": [{"up_to": None, "per_share": 0.005, "min": 1.0, "max_pct": 0.01}],
    "EUR": [{"up_to": None, "pct": 0.0005, "min": 1.25, "max": 29.0}],
    "CHF": [{"up_to": None, "pct": 0.0005, "min": 1.5}],
    "default": [{"up_to": None, "pct": 0.0005, "min": 1.25}],
}
_IBKR_TIERED = {
    "USD": [{"up_to": None, "per_share": 0.0035, "min": 0.35, "max_pct": 0.005}],
    "EUR": [{"up_to": None, "pct": 0.0005, "min": 1.25, "max": 29.0}],
    "CHF": [{"up_to": None, "pct": 0.0005, "min": 1.5}],
    "default": [{"up_to": None, "pct": 0.0005, "min": 1.25}],
}


async def seed_platforms(db: AsyncSession) -> None:
    """Legt fehlende Standard-Plattformen an (idempotent, per Name)."""
    from sqlalchemy import select

    from app.models import TradingPlatform

    result = await db.execute(select(TradingPlatform.name))
    existing = {row[0] for row in result.all()}
    defaults = {
        "Swissquote": {"default": _SWISSQUOTE_DEFAULT, "EUR": _SWISSQUOTE_EUR},
        "IBKR Fixed": _IBKR_FIXED,
        "IBKR Tiered": _IBKR_TIERED,
    }
    added = [name for name, fees in defaults.items() if name not in existing]
    for name in added:
        db.add(TradingPlatform(name=name, fees=defaults[name]))
    if added:
        await db.commit()
        logger.info("Handelsplattformen angelegt: %s", ", ".join(added))
