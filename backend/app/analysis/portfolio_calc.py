"""Portfolio-Bewertung: aktuelle Werte, P/L und Equity-Kurve aus OHLCV."""

from datetime import datetime, timezone

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Position
from app.sources.yahoo import load_ohlcv_df


def position_value(p: Position, current: float | None) -> dict:
    """Bewertung einer Position: offen → aktueller Kurs, geschlossen → Exit.

    Ehrliches P/L: Kaufgebühr erhöht den Einstand, Verkaufsgebühr mindert
    den Erlös (offene Positionen tragen die künftige Verkaufsgebühr noch
    nicht — sie fällt erst bei Realisierung an)."""
    price = p.exit_price if p.exit_date else current
    fee_buy = p.fee_buy or 0.0
    fee_sell = p.fee_sell or 0.0
    value = (price * p.quantity - fee_sell) if price is not None else None
    invested = p.entry_price * p.quantity + fee_buy
    pnl = (value - invested) if value is not None else None
    return {
        "current_price": price,
        "value": round(value, 2) if value is not None else None,
        "invested": round(invested, 2),
        "fees": round(fee_buy + fee_sell, 2),
        "pnl_abs": round(pnl, 2) if pnl is not None else None,
        "pnl_pct": round(pnl / invested * 100, 2) if pnl is not None and invested else None,
    }


async def equity_curve(db: AsyncSession, positions: list[Position]) -> list[dict]:
    """Tageswert des Portfolios seit dem frühesten Einstieg.

    Offene Positionen: Menge × Tagesschluss. Geschlossene Positionen
    behalten nach dem Exit ihren Verkaufswert (als wäre er Cash) — so
    bleibt die Kurve stetig und zeigt die Gesamtentwicklung.
    """
    if not positions:
        return []

    start = min(p.entry_date for p in positions)
    today = datetime.now(timezone.utc)
    days_needed = (today - start).days + 10
    dates = pd.date_range(start=start.date(), end=today.date(), freq="D", tz="UTC")
    total = pd.Series(0.0, index=dates)

    for p in positions:
        df = await load_ohlcv_df(db, p.symbol, days=days_needed)
        if df.empty:
            continue
        # Union-Reindex, damit der letzte Schlusskurs VOR dem Fenster
        # per ffill in Wochenend-/Feiertagslücken propagiert wird.
        closes = df["close"]
        closes = closes.reindex(closes.index.union(dates)).ffill().reindex(dates)
        val = closes * p.quantity
        # Verbleibende NaN (vor erstem Kurs im Fenster): Einstandswert
        val = val.fillna(pd.Series(p.entry_price * p.quantity, index=dates))
        entry = pd.Timestamp(p.entry_date).tz_convert("UTC").normalize()
        val[val.index < entry] = 0.0
        if p.exit_date:
            exit_ts = pd.Timestamp(p.exit_date).tz_convert("UTC").normalize()
            val[val.index > exit_ts] = p.exit_price * p.quantity
        total += val

    out = []
    started = False
    for ts, v in total.items():
        if not started and v == 0:
            continue
        started = True
        out.append({"time": ts.strftime("%Y-%m-%d"), "value": round(float(v), 2)})
    return out
