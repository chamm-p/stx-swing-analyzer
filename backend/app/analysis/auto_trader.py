"""Autonomes Paper-Trading-Portfolio (kind=auto).

Das System handelt seine eigenen Signale unter Rahmenbedingungen aus
``Portfolio.config``:

- ``start_capital``   Startkapital (Paper-Cash)
- ``max_per_trade``   maximales Volumen pro Kauf
- ``max_positions``   maximale Anzahl offener Positionen
- ``min_confidence``  Mindest-Confidence für Signal-Käufe
- ``use_screener``    zusätzlich Screener-BUYs handeln (rein technisch)
- ``enabled``         Trading an/aus

Exits: SELL-Signal für ein gehaltenes Symbol oder Ablauf des
Signal-Horizonts. Kein echtes Geld, keine Order-Ausführung — reine
Simulation zur ehrlichen Forward-Messung der Signalqualität.
FX wird ignoriert (EUR-/USD-Titel werden nominal verrechnet).
"""

import logging
from datetime import timedelta

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Portfolio, Position, ScreenerResult, Signal, utcnow
from app.sources.yahoo import latest_close

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "start_capital": 10000.0,
    "max_per_trade": 1000.0,
    "max_positions": 10,
    "min_confidence": 0.5,
    "use_screener": True,
    "enabled": True,
}

# Nach einem Exit dasselbe Symbol einige Tage nicht erneut kaufen —
# verhindert Kauf/Verkauf-Pingpong um die Schwelle herum.
_REENTRY_COOLDOWN_DAYS = 3
# Nur Signale der letzten 48h als Kaufkandidaten betrachten
_ENTRY_WINDOW_HOURS = 48


async def _open_positions(db: AsyncSession, portfolio_id: int) -> list[Position]:
    result = await db.execute(
        select(Position).where(Position.portfolio_id == portfolio_id,
                               Position.exit_date.is_(None))
    )
    return list(result.scalars().all())


async def _close(db: AsyncSession, pf: Portfolio, p: Position, reason: str) -> bool:
    price = await latest_close(db, p.symbol)
    if price is None:
        return False
    p.exit_price = price
    p.exit_date = utcnow()
    p.notes = f"{p.notes or ''} | Exit: {reason}".strip(" |")
    pf.cash += price * p.quantity
    logger.info("Auto-Portfolio %s: %s verkauft zu %.4f (%s)", pf.name, p.symbol, price, reason)
    return True


async def _run_exits(db: AsyncSession, pf: Portfolio) -> int:
    closed = 0
    for p in await _open_positions(db, pf.id):
        horizon = p.horizon_days or 14
        if utcnow() >= p.entry_date + timedelta(days=horizon):
            closed += await _close(db, pf, p, f"Horizont ({horizon}d) abgelaufen")
            continue
        sell_signal = await db.scalar(
            select(Signal).where(
                Signal.symbol == p.symbol, Signal.action == "SELL",
                Signal.ts > p.entry_date,
            ).order_by(desc(Signal.ts)).limit(1)
        )
        if sell_signal is not None:
            closed += await _close(db, pf, p, "SELL-Signal")
    return closed


async def _recently_traded(db: AsyncSession, portfolio_id: int, symbol: str) -> bool:
    cutoff = utcnow() - timedelta(days=_REENTRY_COOLDOWN_DAYS)
    row = await db.scalar(
        select(Position.id).where(
            Position.portfolio_id == portfolio_id, Position.symbol == symbol,
            Position.exit_date >= cutoff,
        ).limit(1)
    )
    return row is not None


async def _buy_candidates(db: AsyncSession, cfg: dict) -> list[dict]:
    """Kaufkandidaten: Watchlist-Signale (BUY), optional Screener-BUYs.

    Sortiert nach Confidence/Signalstärke — die stärksten zuerst."""
    since = utcnow() - timedelta(hours=_ENTRY_WINDOW_HOURS)
    result = await db.execute(
        select(Signal).where(
            Signal.action == "BUY", Signal.ts >= since,
            Signal.confidence >= cfg["min_confidence"],
        ).order_by(desc(Signal.confidence))
    )
    candidates = [{
        "symbol": s.symbol, "signal_id": s.id,
        "horizon_days": s.horizon_days, "rank": s.confidence,
        "origin": "signal",
    } for s in result.scalars().all()]

    if cfg.get("use_screener", True):
        from sqlalchemy import func
        last_run = await db.scalar(select(func.max(ScreenerResult.run_at)))
        if last_run is not None:
            result = await db.execute(
                select(ScreenerResult).where(
                    ScreenerResult.run_at == last_run, ScreenerResult.action == "BUY",
                ).order_by(desc(ScreenerResult.technical_score))
            )
            candidates += [{
                "symbol": r.symbol, "signal_id": None,
                "horizon_days": 14, "rank": r.technical_score,
                "origin": "screener",
            } for r in result.scalars().all()]

    # Dedupe pro Symbol (stärkster Kandidat gewinnt, Signale vor Screener)
    seen: set[str] = set()
    unique = []
    for c in candidates:
        if c["symbol"] not in seen:
            seen.add(c["symbol"])
            unique.append(c)
    return unique


async def _run_entries(db: AsyncSession, pf: Portfolio, cfg: dict) -> int:
    open_pos = await _open_positions(db, pf.id)
    held = {p.symbol for p in open_pos}
    slots = cfg["max_positions"] - len(open_pos)
    if slots <= 0:
        return 0

    opened = 0
    for cand in await _buy_candidates(db, cfg):
        if slots <= 0 or pf.cash < cfg["max_per_trade"] * 0.5:
            break
        symbol = cand["symbol"]
        if symbol in held or await _recently_traded(db, pf.id, symbol):
            continue
        price = await latest_close(db, symbol)
        if price is None or price <= 0:
            continue
        budget = min(cfg["max_per_trade"], pf.cash)
        quantity = round(budget / price, 6)
        db.add(Position(
            portfolio_id=pf.id, symbol=symbol, quantity=quantity,
            entry_price=price, source="auto", signal_id=cand["signal_id"],
            horizon_days=cand["horizon_days"],
            notes=f"Auto-Kauf ({cand['origin']}, Rank {cand['rank']:+.2f})",
        ))
        pf.cash -= quantity * price
        held.add(symbol)
        slots -= 1
        opened += 1
        logger.info("Auto-Portfolio %s: %s gekauft (%.6f Stk. zu %.4f, %s)",
                    pf.name, symbol, quantity, price, cand["origin"])
    return opened


async def run_auto_portfolios(db: AsyncSession) -> dict:
    """Führt Exits + Entries für alle aktiven Auto-Portfolios aus."""
    result = await db.execute(select(Portfolio).where(Portfolio.kind == "auto"))
    stats = {"closed": 0, "opened": 0}
    for pf in result.scalars().all():
        cfg = {**DEFAULT_CONFIG, **(pf.config or {})}
        if not cfg.get("enabled", True):
            continue
        try:
            stats["closed"] += await _run_exits(db, pf)
            stats["opened"] += await _run_entries(db, pf, cfg)
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Auto-Trading für Portfolio %s fehlgeschlagen", pf.name)
    return stats
