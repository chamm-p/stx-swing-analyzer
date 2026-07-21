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
                 "horizon_days",
                 # Momentum/DTT-Parameter — für die 1:1-Übernahme aus dem Backtest
                 "trailing_stop_atr", "regime_sma", "target_r", "breakeven_r")


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


def score_for_strategy(snapshot: dict, strategy: dict) -> float:
    """Ein-/Ausstiegs-Score gemäß Strategie-Art (1:1 wie im Backtest):
    momentum/dtt nutzen ihre eigenen Score-Funktionen, sonst Mean-Reversion
    mit den Challenger-Parametern."""
    kind = strategy.get("strategy_kind", "meanrev")
    if kind == "momentum":
        from app.analysis.scoring import momentum_score
        return momentum_score(snapshot)[0]
    if kind == "dtt":
        from app.analysis.scoring import dtt_score
        return dtt_score(snapshot)[0]
    from app.analysis.scoring import technical_score
    return technical_score(snapshot, strategy_profile(strategy))[0]


def targets_for_strategy(snapshot: dict, strategy: dict, price: float) -> dict:
    """Ziel/Stop gemäß Strategie-Art — deckt sich mit der Backtest-Engine:
    dtt = Swing-Low-Stop + R-Ziel, momentum = ATR-Stop ohne Fixziel
    (Trailing), meanrev = ATR-Zielzone."""
    from app.analysis.targets import compute_price_targets

    kind = strategy.get("strategy_kind", "meanrev")
    atr = snapshot.get("atr14")
    if kind == "dtt":
        swing = snapshot.get("swing_low")
        stop = swing if (swing and swing < price) else None
        if stop is None:
            s50 = snapshot.get("sma50")
            stop = s50 * 0.995 if (s50 and s50 < price) else price * 0.95
        risk = price - stop
        target_r = float(strategy.get("target_r", 2.0))
        return {"target_price": round(price + target_r * risk, 4) if target_r > 0 else None,
                "stop_price": round(stop, 4)}
    if kind == "momentum":
        stop_atr = float(strategy.get("stop_atr_factor", 1.5))
        return {"target_price": None,  # Trailing-Stop, kein Fixziel
                "stop_price": round(price - stop_atr * atr, 4) if atr else None}
    return compute_price_targets(
        snapshot, "BUY", int(strategy.get("horizon_days", 14)),
        target_atr_factor=float(strategy.get("target_atr_factor", 2.0)),
        stop_atr_factor=float(strategy.get("stop_atr_factor", 1.5))) or {}


async def _strategy_score(db: AsyncSession, symbol: str, strategy: dict) -> float | None:
    """Ein-/Ausstiegs-Score eines Symbols mit Challenger-Parametern
    (Strategie-Art-bewusst)."""
    from app.processing.indicators import compute_indicators
    from app.sources.yahoo import load_ohlcv_df

    df = await load_ohlcv_df(db, symbol, days=450)
    if df.empty or len(df) < 60:
        return None
    snapshot = compute_indicators(df)["snapshot"]
    return score_for_strategy(snapshot, strategy)


class _TradingDisabled(Exception):
    """IBKR-Trading global deaktiviert — Auto-Order nicht ausführbar."""


async def _ibkr_execute(db: AsyncSession, symbol: str, side: str, quantity: float,
                        currency: str | None, take_profit: float | None = None,
                        stop_loss: float | None = None) -> dict:
    """Echte IBKR-Order für den Auto-Trader. PermissionError → _TradingDisabled."""
    from app.broker import ibkr

    try:
        return await ibkr.place_order(
            db, symbol=symbol, side=side, quantity=quantity, order_type="MKT",
            take_profit=take_profit, stop_loss=stop_loss, currency=currency)
    except PermissionError as e:
        raise _TradingDisabled(str(e))


async def _run_exits(db: AsyncSession, pf: Portfolio, cfg: dict,
                     proposals: list | None = None) -> int:
    """Exit-Prioritäten: Stop-Loss → Take-Profit → Exit-Signal → Horizont.

    Grundlage sind Tagesschlusskurse — Stop/Ziel werden am Close geprüft,
    nicht intraday. Ausführung je nach cfg["execution"]: paper (simuliert),
    manual (nur Vorschlag) oder ibkr (echte SELL-Order)."""
    from app.analysis.fees import portfolio_fee
    from app.models import Asset

    strategy = cfg.get("strategy")
    execution = cfg.get("execution", "paper")

    # Im IBKR-Auto-Modus verwaltet die Bracket-Order (Ziel/Stop, beim Kauf
    # platziert) die Exits direkt bei IBKR — unser Trader mischt sich nicht
    # ein (verhindert Doppel-Verkauf und Konflikte mit dem Bestands-Sync).
    if execution == "ibkr":
        return 0

    closed = 0
    for p in await _open_positions(db, pf.id):
        price = await latest_close(db, p.symbol)
        if price is None:
            continue
        asset = await db.get(Asset, p.symbol)
        fee = await portfolio_fee(db, pf, asset.currency if asset else None,
                                  price * p.quantity, quantity=p.quantity)

        # Trailing-Stop/Break-even (Momentum/DTT) vor der Stop-Prüfung
        if strategy:
            await _update_stop(db, p, strategy)

        # Exit-Grund ermitteln (Prioritäten)
        reason = None
        if p.stop_price and price <= p.stop_price:
            reason = f"Stop-Loss ({p.stop_price}) erreicht"
        elif p.target_price and price >= p.target_price:
            reason = f"Kursziel ({p.target_price}) erreicht"
        else:
            horizon = p.horizon_days or 14
            if utcnow() >= p.entry_date + timedelta(days=horizon):
                reason = f"Horizont ({horizon}d) abgelaufen"
            elif strategy:
                score = await _strategy_score(db, p.symbol, strategy)
                if score is not None and score <= -float(strategy.get("threshold", 0.35)):
                    reason = f"Strategie-Exit (Score {score:+.2f})"
            else:
                sell = await db.scalar(
                    select(Signal).where(
                        Signal.symbol == p.symbol, Signal.action == "SELL",
                        Signal.ts > p.entry_date,
                    ).order_by(desc(Signal.ts)).limit(1))
                if sell is not None:
                    reason = "SELL-Signal"
        if not reason:
            continue

        if execution == "manual":
            if proposals is not None:
                proposals.append(f"VERKAUFEN {p.quantity} × {p.symbol} @ ~{round(price, 2)} "
                                 f"— {reason}")
            continue
        exit_price = price
        if execution == "ibkr":
            try:
                order = await _ibkr_execute(db, p.symbol, "SELL", p.quantity,
                                            asset.currency if asset else None)
                exit_price = order.get("avg_fill_price") or price
                reason += " [IBKR]"
            except _TradingDisabled:
                logger.warning("Auto-Portfolio %s: IBKR-Trading aus — Verkauf %s "
                               "nicht ausgeführt", pf.name, p.symbol)
                continue
            except Exception as e:
                logger.error("Auto-Portfolio %s: IBKR-Verkauf %s fehlgeschlagen: %s",
                             pf.name, p.symbol, e)
                continue
        _do_close(pf, p, exit_price, reason, fee)
        closed += 1
    return closed


async def _update_stop(db: AsyncSession, p: Position, strategy: dict) -> None:
    """Trailing-Stop (Momentum) bzw. Break-even (DTT) nachziehen — der
    Hochwasserstand wird aus der Kurshistorie seit Einstieg abgeleitet
    (keine Extra-Spalte nötig). Der Stop wird nie gesenkt."""
    kind = strategy.get("strategy_kind", "meanrev")
    trailing = float(strategy.get("trailing_stop_atr", 0) or 0)
    breakeven = float(strategy.get("breakeven_r", 0) or 0)
    if kind not in ("momentum", "dtt") or (trailing <= 0 and breakeven <= 0):
        return
    from app.processing.indicators import atr as atr_fn
    from app.sources.yahoo import load_ohlcv_df

    df = await load_ohlcv_df(db, p.symbol, days=450)
    if df.empty or p.entry_date is None:
        return
    since = df[df.index >= p.entry_date]
    if since.empty:
        return
    high_water = float(since["high"].max())
    if kind == "momentum" and trailing > 0:
        try:
            atr_val = float(atr_fn(df).iloc[-1])
        except Exception:
            return
        trailed = high_water - trailing * atr_val
        if p.stop_price is None or trailed > p.stop_price:
            p.stop_price = round(trailed, 4)
    elif kind == "dtt" and breakeven > 0 and p.stop_price and p.stop_price < p.entry_price:
        risk = p.entry_price - p.stop_price
        if high_water >= p.entry_price + breakeven * risk:
            p.stop_price = round(p.entry_price, 4)  # Break-even


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

    # Segment-Scope: ein DAX-Challenger handelt nur DAX (1:1 zum Backtest).
    # "+" gruppiert (US+NASDAQ100); leer/None = ganzes Universum.
    q = select(UniverseSymbol.symbol).where(UniverseSymbol.active == True)  # noqa: E712
    segment = str(strategy.get("segment") or "").strip()
    if segment and segment.lower() != "alle":
        segs = [t for t in segment.upper().replace(" ", "+").split("+") if t]
        q = q.where(UniverseSymbol.segment.in_(segs))
    result = await db.execute(q)

    candidates: list[dict] = []
    for (symbol,) in result.all():
        df = await load_ohlcv_df(db, symbol, days=450)
        if df.empty or len(df) < 60:
            continue
        snapshot = compute_indicators(df)["snapshot"]
        score = score_for_strategy(snapshot, strategy)
        if score < threshold:
            continue
        price = snapshot.get("close")
        targets = targets_for_strategy(snapshot, strategy, price) if price else {}
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


async def _run_entries(db: AsyncSession, pf: Portfolio, cfg: dict,
                       proposals: list | None = None) -> int:
    open_pos = await _open_positions(db, pf.id)
    held = {p.symbol for p in open_pos}
    slots = cfg["max_positions"] - len(open_pos)
    if slots <= 0:
        return 0

    execution = cfg.get("execution", "paper")
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

        # Goldene Regel: kein Kauf mit magerem Chance-Risiko-Verhältnis.
        # Challenger sind ausgenommen — sie handeln bewusst ihre eigene
        # Ziel-/Stop-Geometrie (sonst überstimmt der globale Guard die
        # Strategie, die er testen soll).
        cand_crv = crv(price, target_price, stop_price)
        if (cand.get("origin") != "strategy" and cand_crv is not None
                and cand_crv < cfg["min_crv"]):
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

        # Manuell: nur vorschlagen, nichts ausführen
        if execution == "manual":
            if proposals is not None:
                proposals.append(f"KAUFEN {quantity} × {symbol} @ ~{round(price, 2)} "
                                 f"(Ziel {target_price}, Stop {stop_price}) — {cand['origin']}")
            held.add(symbol)
            slots -= 1
            continue

        entry_price, src = price, "auto"
        if execution == "ibkr":
            try:
                order = await _ibkr_execute(
                    db, symbol, "BUY", quantity, asset.currency if asset else None,
                    take_profit=target_price, stop_loss=stop_price)
            except _TradingDisabled:
                logger.warning("Auto-Portfolio %s: IBKR-Trading aus — Käufe gestoppt", pf.name)
                break
            except Exception as e:
                logger.error("Auto-Portfolio %s: IBKR-Kauf %s fehlgeschlagen: %s",
                             pf.name, symbol, e)
                continue
            entry_price = order.get("avg_fill_price") or price
            src = "ibkr_auto"

        db.add(Position(
            portfolio_id=pf.id, symbol=symbol, quantity=quantity,
            entry_price=entry_price, source=src, signal_id=cand["signal_id"],
            horizon_days=cand["horizon_days"],
            target_price=target_price, stop_price=stop_price, fee_buy=fee,
            notes=f"Auto-Kauf ({cand['origin']}, Rank {cand['rank']:+.2f}, "
                  f"Ziel {target_price}, Stop {stop_price}"
                  + (", IBKR" if src == "ibkr_auto" else "") + ")",
        ))
        pf.cash -= quantity * entry_price + fee
        held.add(symbol)
        slots -= 1
        opened += 1
        logger.info("Auto-Portfolio %s: %s gekauft (%.6f Stk. zu %.4f, %s%s)",
                    pf.name, symbol, quantity, entry_price, cand["origin"],
                    " [IBKR]" if src == "ibkr_auto" else "")
    return opened


async def run_auto_portfolios(db: AsyncSession) -> dict:
    """Führt Exits + Entries für alle aktiven Auto-Portfolios aus."""
    from app.analysis.scoring import load_champion
    from app.config import get_settings

    s = get_settings()
    await load_champion(db)
    result = await db.execute(select(Portfolio).where(Portfolio.kind == "auto"))
    stats = {"closed": 0, "opened": 0}
    for pf in result.scalars().all():
        cfg = {**DEFAULT_CONFIG,
               "risk_pct": s.risk_per_trade_pct, "min_crv": s.swing_min_crv,
               **(pf.config or {})}
        if not cfg.get("enabled", True):
            continue
        proposals: list[str] = []
        try:
            stats["closed"] += await _run_exits(db, pf, cfg, proposals)
            stats["opened"] += await _run_entries(db, pf, cfg, proposals)
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Auto-Trading für Portfolio %s fehlgeschlagen", pf.name)
            continue
        if proposals:  # execution="manual" → Vorschläge per Alert
            await _notify_proposals(db, pf, proposals)
    return stats


async def _notify_proposals(db: AsyncSession, pf: Portfolio, proposals: list[str]) -> None:
    """Manual-Modus: vorgeschlagene Trades melden (Telegram/E-Mail).
    24h-Dedupe verhindert stündliche Wiederholung desselben Vorschlags."""
    import asyncio
    import hashlib

    from app.alerts.dispatcher import send_email_sync, send_telegram
    from app.services_redis import get_redis
    from app.services_settings import load_settings

    digest = hashlib.sha256("|".join(sorted(proposals)).encode()).hexdigest()[:16]
    key = f"proposals:{pf.id}:{digest}"
    r = get_redis()
    if await r.get(key):
        return
    await r.set(key, "1", ex=86400)

    text = (f"📋 Handelsvorschläge für {pf.name} (manuell):\n"
            + "\n".join(f"• {p}" for p in proposals)
            + "\n\nAusführen über den Kauf-/Verkauf-Dialog (routet zu IBKR).")
    comm = await load_settings(db, "comm")
    if comm.get("telegram_bot_token") and comm.get("telegram_chat_id"):
        try:
            await send_telegram(comm, text)
        except Exception as e:
            logger.error("Vorschlags-Telegram fehlgeschlagen: %s", e)
    if comm.get("smtp_host") and comm.get("alert_email_to"):
        try:
            await asyncio.to_thread(send_email_sync, comm,
                                    f"[stx] Handelsvorschläge {pf.name}", text)
        except Exception as e:
            logger.error("Vorschlags-E-Mail fehlgeschlagen: %s", e)
