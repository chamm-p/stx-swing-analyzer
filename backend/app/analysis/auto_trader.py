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

from app.analysis.position_sizing import crv, portfolio_market_value, risk_based_quantity
from app.models import Portfolio, Position, ScreenerResult, Signal, utcnow
from app.sources.yahoo import ensure_asset, latest_close

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "start_capital": 10000.0,
    "max_per_trade": 1000.0,
    "max_positions": 10,
    "min_confidence": 0.5,
    "use_screener": True,
    "enabled": True,
}

# Felder einer Challenger-Strategie (aus Backtest übernommen)
STRATEGY_KEYS = ("rsi_oversold", "rsi_overbought", "rsi_scale", "macd_scale",
                 "w_rsi", "w_macd", "w_bollinger", "w_trend",
                 "threshold", "target_atr_factor", "stop_atr_factor",
                 "horizon_days")


def strategy_profile(strategy: dict):
    """ScoringProfile aus einer Challenger-Strategie (Defaults = stock)."""
    from app.analysis.scoring import PROFILES, ScoringProfile
    base = PROFILES["stock"]
    return ScoringProfile(
        name="challenger",
        rsi_oversold=float(strategy.get("rsi_oversold", base.rsi_oversold)),
        rsi_overbought=float(strategy.get("rsi_overbought", base.rsi_overbought)),
        rsi_scale=float(strategy.get("rsi_scale", base.rsi_scale)),
        macd_scale=float(strategy.get("macd_scale", base.macd_scale)),
        w_rsi=float(strategy.get("w_rsi", base.w_rsi)),
        w_macd=float(strategy.get("w_macd", base.w_macd)),
        w_bollinger=float(strategy.get("w_bollinger", base.w_bollinger)),
        w_trend=float(strategy.get("w_trend", base.w_trend)),
    )

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


def _do_close(pf: Portfolio, p: Position, price: float, reason: str,
              fee: float = 0.0) -> None:
    p.exit_price = price
    p.exit_date = utcnow()
    p.fee_sell = fee
    p.notes = f"{p.notes or ''} | Exit: {reason}".strip(" |")
    pf.cash += price * p.quantity - fee
    logger.info("Auto-Portfolio %s: %s verkauft zu %.4f (%s, Gebühr %.2f)",
                pf.name, p.symbol, price, reason, fee)


async def _strategy_score(db: AsyncSession, symbol: str, strategy: dict) -> float | None:
    """Technischer Score eines Symbols mit Challenger-Parametern."""
    from app.analysis.scoring import technical_score
    from app.processing.indicators import compute_indicators
    from app.sources.yahoo import load_ohlcv_df

    df = await load_ohlcv_df(db, symbol, days=450)
    if df.empty or len(df) < 60:
        return None
    snapshot = compute_indicators(df)["snapshot"]
    score, _ = technical_score(snapshot, strategy_profile(strategy))
    return score


async def _run_exits(db: AsyncSession, pf: Portfolio, cfg: dict) -> int:
    """Exit-Prioritäten: Stop-Loss → Take-Profit → Exit-Signal → Horizont.

    Grundlage sind Tagesschlusskurse (Paper-Trading) — Stop/Ziel werden
    also am Close geprüft, nicht intraday. Challenger-Portfolios nutzen
    ihr eigenes Scoring statt der globalen SELL-Signale."""
    from app.analysis.fees import portfolio_fee
    from app.models import Asset

    strategy = cfg.get("strategy")

    closed = 0
    for p in await _open_positions(db, pf.id):
        price = await latest_close(db, p.symbol)
        if price is None:
            continue
        asset = await db.get(Asset, p.symbol)
        fee = await portfolio_fee(db, pf, asset.currency if asset else None,
                                  price * p.quantity, quantity=p.quantity)

        if p.stop_price and price <= p.stop_price:
            _do_close(pf, p, price, f"Stop-Loss ({p.stop_price}) erreicht", fee)
            closed += 1
            continue
        if p.target_price and price >= p.target_price:
            _do_close(pf, p, price, f"Kursziel ({p.target_price}) erreicht", fee)
            closed += 1
            continue

        horizon = p.horizon_days or 14
        if utcnow() >= p.entry_date + timedelta(days=horizon):
            _do_close(pf, p, price, f"Horizont ({horizon}d) abgelaufen", fee)
            closed += 1
            continue
        if strategy:
            score = await _strategy_score(db, p.symbol, strategy)
            if score is not None and score <= -float(strategy.get("threshold", 0.35)):
                _do_close(pf, p, price, f"Strategie-Exit (Score {score:+.2f})", fee)
                closed += 1
            continue
        sell_signal = await db.scalar(
            select(Signal).where(
                Signal.symbol == p.symbol, Signal.action == "SELL",
                Signal.ts > p.entry_date,
            ).order_by(desc(Signal.ts)).limit(1)
        )
        if sell_signal is not None:
            _do_close(pf, p, price, "SELL-Signal", fee)
            closed += 1
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


async def _strategy_candidates(db: AsyncSession, strategy: dict) -> list[dict]:
    """Challenger-Kandidaten: eigenes Scoring über das Universum.

    Bewusst unabhängig von globalen Signalen/Screener — der Challenger
    handelt SEINE Parameter, sonst wäre der Vergleich wertlos."""
    from app.analysis.targets import compute_price_targets
    from app.models import UniverseSymbol
    from app.processing.indicators import compute_indicators
    from app.sources.yahoo import load_ohlcv_df

    threshold = float(strategy.get("threshold", 0.35))
    horizon = int(strategy.get("horizon_days", 14))
    profile = strategy_profile(strategy)
    from app.analysis.scoring import technical_score

    result = await db.execute(
        select(UniverseSymbol.symbol).where(UniverseSymbol.active == True)  # noqa: E712
    )
    candidates: list[dict] = []
    for (symbol,) in result.all():
        df = await load_ohlcv_df(db, symbol, days=450)
        if df.empty or len(df) < 60:
            continue
        snapshot = compute_indicators(df)["snapshot"]
        score, _ = technical_score(snapshot, profile)
        if score < threshold:
            continue
        targets = compute_price_targets(
            snapshot, "BUY", horizon,
            target_atr_factor=float(strategy.get("target_atr_factor", 2.0)),
            stop_atr_factor=float(strategy.get("stop_atr_factor", 1.5)),
        ) or {}
        candidates.append({
            "symbol": symbol, "signal_id": None, "horizon_days": horizon,
            "rank": score, "origin": "strategy",
            "target_price": targets.get("target_price"),
            "stop_price": targets.get("stop_price"),
        })
    candidates.sort(key=lambda c: c["rank"], reverse=True)
    return candidates


async def _buy_candidates(db: AsyncSession, cfg: dict) -> list[dict]:
    """Kaufkandidaten: Watchlist-Signale (BUY), optional Screener-BUYs.

    Sortiert nach Confidence/Signalstärke — die stärksten zuerst."""
    if cfg.get("strategy"):
        return await _strategy_candidates(db, cfg["strategy"])

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

    # Basis für die 1%-Regel: aktueller Gesamtwert (Cash + Positionen)
    total_value = await portfolio_market_value(db, pf, open_pos)

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
        asset = None
        try:
            # Stammdaten anlegen (Name, Typ, News-Keywords), falls das
            # Symbol nur aus dem Screener-Universum kommt
            asset = await ensure_asset(db, symbol)
        except Exception as e:
            logger.warning("Asset-Stammdaten für %s nicht ladbar: %s", symbol, e)

        # Take-Profit/Stop VOR der Größenbestimmung: vom Kandidaten
        # (Strategie), aus dem Signal, oder frisch aus den Indikatoren —
        # die 1%-Regel und der CRV-Guard brauchen den Stop.
        target_price = cand.get("target_price")
        stop_price = cand.get("stop_price")
        if target_price is None and cand["signal_id"]:
            sig = await db.get(Signal, cand["signal_id"])
            if sig:
                target_price, stop_price = sig.target_price, sig.stop_price
        if target_price is None:
            from app.analysis.targets import compute_price_targets
            from app.processing.indicators import compute_indicators
            from app.sources.yahoo import load_ohlcv_df
            df = await load_ohlcv_df(db, symbol)
            if not df.empty:
                snapshot = compute_indicators(df)["snapshot"]
                targets = compute_price_targets(snapshot, "BUY", cand["horizon_days"] or 14)
                if targets:
                    target_price = targets["target_price"]
                    stop_price = targets["stop_price"]

        # Goldene Regel: kein Kauf mit magerem Chance-Risiko-Verhältnis
        cand_crv = crv(price, target_price, stop_price)
        if cand_crv is not None and cand_crv < cfg["min_crv"]:
            logger.info("Auto-Portfolio %s: %s übersprungen (CRV %.2f < %.2f)",
                        pf.name, symbol, cand_crv, cfg["min_crv"])
            continue

        from app.analysis.fees import portfolio_fee
        budget = min(cfg["max_per_trade"], pf.cash)
        est_quantity = budget / price
        # 1%-Regel: Stückzahl so deckeln, dass ein Ausstoppen höchstens
        # risk_pct% des Portfoliowerts kostet
        risk_qty = risk_based_quantity(total_value, price, stop_price, cfg["risk_pct"])
        if risk_qty is not None:
            est_quantity = min(est_quantity, risk_qty)
        # per-Share-Gebühren brauchen die Stückzahl — Näherung über die
        # ungekürzte Menge (überschätzt minimal, sprengt nie das Cash)
        fee = await portfolio_fee(db, pf, asset.currency if asset else None,
                                  est_quantity * price, quantity=est_quantity)
        quantity = round(max(min(est_quantity, (budget - fee) / price), 0), 6)
        if quantity <= 0:
            continue

        db.add(Position(
            portfolio_id=pf.id, symbol=symbol, quantity=quantity,
            entry_price=price, source="auto", signal_id=cand["signal_id"],
            horizon_days=cand["horizon_days"],
            target_price=target_price, stop_price=stop_price, fee_buy=fee,
            notes=f"Auto-Kauf ({cand['origin']}, Rank {cand['rank']:+.2f}, "
                  f"Ziel {target_price}, Stop {stop_price})",
        ))
        pf.cash -= quantity * price + fee
        held.add(symbol)
        slots -= 1
        opened += 1
        logger.info("Auto-Portfolio %s: %s gekauft (%.6f Stk. zu %.4f, %s)",
                    pf.name, symbol, quantity, price, cand["origin"])
    return opened


async def run_auto_portfolios(db: AsyncSession) -> dict:
    """Führt Exits + Entries für alle aktiven Auto-Portfolios aus."""
    from app.config import get_settings

    s = get_settings()
    result = await db.execute(select(Portfolio).where(Portfolio.kind == "auto"))
    stats = {"closed": 0, "opened": 0}
    for pf in result.scalars().all():
        cfg = {**DEFAULT_CONFIG,
               "risk_pct": s.risk_per_trade_pct, "min_crv": s.swing_min_crv,
               **(pf.config or {})}
        if not cfg.get("enabled", True):
            continue
        try:
            stats["closed"] += await _run_exits(db, pf, cfg)
            stats["opened"] += await _run_entries(db, pf, cfg)
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Auto-Trading für Portfolio %s fehlgeschlagen", pf.name)
    return stats
