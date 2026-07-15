"""Reports: Schweizer Steuerreport für den Aktienhandel.

Liefert je Steuerjahr (a) alle realisierten Trades mit Gewinn/Verlust
inkl. Gebühren und (b) das Wertschriftenverzeichnis per 31.12.
(Bestände mit letztem Schlusskurs des Jahres).

Ehrliche Grenzen: Werte in Handelswährung (keine CHF-Umrechnung — dafür
gilt die ESTV-Jahresendkursliste) und ohne Dividenden/Quellensteuer —
die liefert der IBKR-/Broker-Jahresauszug.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_user
from app.database import get_db
from app.models import Asset, Ohlcv, Portfolio, Position

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


async def _close_at(db: AsyncSession, symbol: str, at: datetime) -> float | None:
    return await db.scalar(
        select(Ohlcv.close).where(Ohlcv.symbol == symbol, Ohlcv.ts <= at)
        .order_by(desc(Ohlcv.ts)).limit(1))


@router.get("/reports/tax/{year}")
async def tax_report(year: int, portfolio_id: int | None = None,
                     db: AsyncSession = Depends(get_db)):
    if not 2000 <= year <= 2100:
        raise HTTPException(status_code=422, detail="Ungültiges Jahr")
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

    q = select(Portfolio)
    if portfolio_id is not None:
        q = q.where(Portfolio.id == portfolio_id)
    else:
        q = q.where(Portfolio.kind == "real")  # Steuer: nur echte Depots
    portfolios = (await db.execute(q.order_by(Portfolio.id))).scalars().all()
    if not portfolios:
        raise HTTPException(status_code=404, detail="Kein (echtes) Portfolio gefunden")

    currencies: dict[str, str | None] = {}

    async def currency(symbol: str) -> str | None:
        if symbol not in currencies:
            asset = await db.get(Asset, symbol)
            currencies[symbol] = asset.currency if asset else None
        return currencies[symbol]

    trades, holdings = [], []
    total_pnl = total_fees = 0.0
    for pf in portfolios:
        positions = (await db.execute(
            select(Position).where(Position.portfolio_id == pf.id))).scalars().all()
        for p in positions:
            # --- realisierte Trades im Steuerjahr ---
            if p.exit_date is not None and start <= p.exit_date <= end:
                proceeds = round(p.quantity * (p.exit_price or 0), 2)
                cost = round(p.quantity * p.entry_price, 2)
                fees = round((p.fee_buy or 0) + (p.fee_sell or 0), 2)
                pnl = round(proceeds - cost - fees, 2)
                total_pnl += pnl
                total_fees += fees
                trades.append({
                    "portfolio": pf.name, "symbol": p.symbol,
                    "currency": await currency(p.symbol),
                    "quantity": p.quantity,
                    "entry_date": p.entry_date.date().isoformat() if p.entry_date else None,
                    "entry_price": p.entry_price,
                    "exit_date": p.exit_date.date().isoformat(),
                    "exit_price": p.exit_price,
                    "proceeds": proceeds, "cost": cost, "fees": fees, "pnl": pnl,
                })
            # --- Bestand per 31.12. (Wertschriftenverzeichnis) ---
            if (p.entry_date is not None and p.entry_date <= end
                    and (p.exit_date is None or p.exit_date > end)):
                close = await _close_at(db, p.symbol, end)
                holdings.append({
                    "portfolio": pf.name, "symbol": p.symbol,
                    "currency": await currency(p.symbol),
                    "quantity": p.quantity,
                    "entry_price": p.entry_price,
                    "year_end_close": close,
                    "year_end_value": round(p.quantity * close, 2) if close else None,
                })

    trades.sort(key=lambda t: t["exit_date"])
    holdings.sort(key=lambda h: (h["portfolio"], h["symbol"]))
    return {
        "year": year,
        "portfolios": [pf.name for pf in portfolios],
        "trades": trades,
        "holdings": holdings,
        "totals": {
            "realized_pnl": round(total_pnl, 2),
            "fees": round(total_fees, 2),
            "num_trades": len(trades),
            "holdings_value": round(sum(h["year_end_value"] or 0 for h in holdings), 2),
        },
        "notes": [
            "Beträge in der jeweiligen Handelswährung — CHF-Umrechnung gemäß "
            "ESTV-Jahresendkursliste selbst vornehmen.",
            "Dividenden und Quellensteuern sind hier nicht erfasst "
            "(Broker-Jahresauszug beiziehen).",
        ],
    }
