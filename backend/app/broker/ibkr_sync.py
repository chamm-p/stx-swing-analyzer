"""IBKR-Portfolio-Sync: echte IBKR-Bestände in ein App-Portfolio spiegeln.

Read-only über die Web-API (funktioniert auch ohne „Orders erlauben").
Abgleich je verknüpftem Portfolio (config.ibkr_sync = true, kind=real):

- Neue IBKR-Position       → App-Position mit echtem Einstand (avgCost,
                             Kommission bereits enthalten → fee_buy = 0)
- Stückzahl geändert       → App-Position angepasst
- Bei IBKR verschwunden    → App-Position geschlossen (letzter Close als
                             Näherung — die TWS-API liefert keine
                             historischen Fill-Preise)
- Cash                     → TotalCashValue des Kontos (Basiswährung)

Der Sync legt nie Orders — er liest nur.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Portfolio, Position, utcnow

logger = logging.getLogger(__name__)


def yahoo_symbol(ticker: str | None, currency: str | None,
                 asset_class: str | None) -> str | None:
    """IBKR-Positionsdaten (Web-API) → Yahoo-Notation."""
    sym = (ticker or "").upper().strip()
    if not sym or (asset_class or "STK") != "STK":
        return None
    if currency == "EUR":
        return sym + ".DE"  # XETRA/IBIS
    if currency == "USD":
        return sym.replace(" ", "-")  # BRK B → BRK-B
    logger.info("IBKR-Sync: %s (%s) nicht gemappt", sym, currency)
    return None


async def fetch_ibkr_state(db: AsyncSession) -> dict:
    """Positionen + Cash read-only über die Web-API lesen."""
    from app.broker.ibkr import status

    st = await status(db)
    positions: dict = {}
    for p in st.get("positions") or []:
        sym = yahoo_symbol(p.get("symbol"), p.get("currency"), p.get("asset_class"))
        if sym and p.get("quantity"):
            # avgCost ist der Einstand PRO STÜCK inkl. Kommission
            positions[sym] = {"quantity": float(p["quantity"]),
                              "avg_cost": float(p.get("avg_cost") or 0)}
    cash_entry = (st.get("summary") or {}).get("TotalCashValue") or {}
    cash = cash_entry.get("value")
    return {"positions": positions,
            "cash": float(cash) if cash is not None else None}


async def _reconcile(db: AsyncSession, pf: Portfolio, state: dict) -> dict:
    from app.sources.yahoo import ensure_asset, latest_close, sync_ohlcv

    open_pos = (await db.execute(
        select(Position).where(Position.portfolio_id == pf.id,
                               Position.exit_date.is_(None)))).scalars().all()
    by_symbol = {p.symbol: p for p in open_pos}
    ib_positions: dict = state["positions"]
    stats = {"added": 0, "updated": 0, "closed": 0}

    for sym, ib_pos in ib_positions.items():
        existing = by_symbol.get(sym)
        if existing is None:
            try:
                await ensure_asset(db, sym)
                await sync_ohlcv(db, sym)
            except Exception as e:
                logger.warning("IBKR-Sync: Stammdaten %s nicht ladbar: %s", sym, e)
            db.add(Position(
                portfolio_id=pf.id, symbol=sym, quantity=ib_pos["quantity"],
                entry_price=ib_pos["avg_cost"], source="ibkr",
                notes="IBKR-Sync (Einstand = avgCost inkl. Kommission; "
                      "Kaufdatum unbekannt)",
            ))
            stats["added"] += 1
        elif abs(existing.quantity - ib_pos["quantity"]) > 1e-6:
            existing.notes = ((existing.notes or "") +
                              f" · IBKR-Sync: Stückzahl {existing.quantity} → "
                              f"{ib_pos['quantity']}").strip(" ·")
            existing.quantity = ib_pos["quantity"]
            existing.entry_price = ib_pos["avg_cost"]
            stats["updated"] += 1

    for sym, pos in by_symbol.items():
        if sym not in ib_positions:
            close = await latest_close(db, sym)
            pos.exit_price = close or pos.entry_price
            pos.exit_date = utcnow()
            pos.notes = ((pos.notes or "") +
                         " · IBKR-Sync: extern verkauft (Exit = letzter Close, "
                         "Näherung)").strip(" ·")
            stats["closed"] += 1

    if state.get("cash") is not None:
        pf.cash = state["cash"]
    await db.commit()
    return stats


async def sync_ibkr_portfolios(db: AsyncSession) -> dict:
    """Alle verknüpften Portfolios (config.ibkr_sync) abgleichen."""
    result = await db.execute(select(Portfolio).where(Portfolio.kind == "real"))
    linked = [pf for pf in result.scalars().all()
              if (pf.config or {}).get("ibkr_sync")]
    if not linked:
        return {"linked": 0}

    state = await fetch_ibkr_state(db)
    totals = {"linked": len(linked), "added": 0, "updated": 0, "closed": 0}
    for pf in linked:
        stats = await _reconcile(db, pf, state)
        for k in ("added", "updated", "closed"):
            totals[k] += stats[k]
        logger.info("IBKR-Sync %s: %s", pf.name, stats)
    return totals
