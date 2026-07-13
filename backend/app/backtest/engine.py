"""Backtest-Engine: tagesweise Simulation der Strategie ohne Look-Ahead.

Ausführungsmodell (konservativ, dokumentiert):
- Signale werden auf Basis des Tagesschlusses t berechnet.
- Einstiege füllen zum OPEN des nächsten Handelstags (+ Slippage).
- Stop/Ziel werden intraday über High/Low geprüft; berühren beide am
  selben Tag, zählt der STOP zuerst (konservativ). Fills zum Stop-/
  Zielkurs (∓ Slippage).
- Horizont-Ablauf und Exit-Signal (Composite <= -Schwelle) füllen zum
  Schlusskurs des Tages.
- Gebühren nach Plattform-Staffel auf jedes Fill-Volumen.

Die Engine ist pur (keine DB, kein LLM): Eingabe sind OHLCV-DataFrames.
``score_fn`` ist injizierbar — Tests prüfen damit die Trade-Mechanik
deterministisch, produktiv rechnet das technische Scoring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

import pandas as pd

from app.analysis.fees import compute_fee
from app.analysis.scoring import technical_score
from app.analysis.targets import compute_price_targets
from app.backtest.params import StrategyConfig
from app.processing.indicators import atr, bollinger, macd, rsi


def indicator_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Alle benötigten Indikator-Serien einmal vektorisieren."""
    close = df["close"]
    out = pd.DataFrame(index=df.index)
    out["open"] = df["open"]
    out["high"] = df["high"]
    out["low"] = df["low"]
    out["close"] = close
    out["rsi14"] = rsi(close)
    _, _, hist = macd(close)
    out["macd_hist"] = hist
    out["macd_hist_prev"] = hist.shift(1)
    _, out["bb_upper"], out["bb_lower"] = bollinger(close)
    out["sma50"] = close.rolling(50).mean()
    out["sma200"] = close.rolling(200).mean()
    out["atr14"] = atr(df)
    out["high_60d"] = df["high"].rolling(60).max()
    out["low_60d"] = df["low"].rolling(60).min()
    return out


@dataclass
class Trade:
    symbol: str
    entry_date: datetime
    entry_price: float
    quantity: float
    fee_buy: float = 0.0
    exit_date: datetime | None = None
    exit_price: float | None = None
    fee_sell: float = 0.0
    reason: str | None = None
    target_price: float | None = None
    stop_price: float | None = None

    @property
    def pnl(self) -> float | None:
        if self.exit_price is None:
            return None
        return (self.exit_price - self.entry_price) * self.quantity \
            - self.fee_buy - self.fee_sell


@dataclass
class BacktestResult:
    config: StrategyConfig
    equity: pd.Series           # Tageswert Cash + offene Positionen
    trades: list[Trade]
    cash_final: float
    warnings: list[str] = field(default_factory=list)


def run_backtest(
    data: dict[str, pd.DataFrame],
    config: StrategyConfig,
    currencies: dict[str, str] | None = None,
    score_fn: Callable[[str, dict], float] | None = None,
) -> BacktestResult:
    """Simuliert die Strategie über die gemeinsame Zeitachse aller Symbole."""
    profile = config.profile()
    currencies = currencies or {}
    slip = config.slippage_bps / 10_000.0

    def score(symbol: str, snapshot: dict) -> float:
        if score_fn is not None:
            return score_fn(symbol, snapshot)
        value, _ = technical_score(snapshot, profile)
        return value

    frames: dict[str, pd.DataFrame] = {}
    warnings: list[str] = []
    for symbol, df in data.items():
        if len(df) <= config.warmup_days + 5:
            warnings.append(f"{symbol}: zu wenig Historie ({len(df)} Kerzen) — übersprungen")
            continue
        frames[symbol] = indicator_frame(df)

    all_dates = sorted(set().union(*[set(f.index) for f in frames.values()])) if frames else []
    # Warmup: erst handeln, wenn die längsten Fenster (SMA200) gefüllt sind
    first_tradeable: dict[str, pd.Timestamp] = {
        s: f.index[config.warmup_days] for s, f in frames.items()
    }

    cash = config.start_capital
    open_trades: dict[str, Trade] = {}
    closed: list[Trade] = []
    pending_entries: list[str] = []
    last_exit: dict[str, pd.Timestamp] = {}
    equity_index: list[pd.Timestamp] = []
    equity_values: list[float] = []
    last_close: dict[str, float] = {}

    def fee_for(symbol: str, volume: float) -> float:
        return compute_fee(config.fees, currencies.get(symbol), volume)

    def close_trade(trade: Trade, date: pd.Timestamp, price: float, reason: str) -> None:
        nonlocal cash
        fee = fee_for(trade.symbol, price * trade.quantity)
        trade.exit_date = date
        trade.exit_price = price
        trade.fee_sell = fee
        trade.reason = reason
        cash += price * trade.quantity - fee
        last_exit[trade.symbol] = date
        closed.append(trade)
        del open_trades[trade.symbol]

    for t in all_dates:
        # 0) Schlusskurse für die Tagesbewertung aktualisieren (ALLE
        #    Symbole — auch gehaltene, sonst bewertet Equity mit
        #    veralteten Kursen)
        for symbol, frame in frames.items():
            if t in frame.index:
                last_close[symbol] = float(frame.at[t, "close"])

        # 1) Ausstehende Einstiege zum heutigen Open füllen
        for symbol in pending_entries:
            frame = frames[symbol]
            if t not in frame.index or symbol in open_trades:
                continue
            if len(open_trades) >= config.max_positions or cash < config.position_size * 0.5:
                continue
            row = frame.loc[t]
            price = float(row["open"]) * (1 + slip)
            if price <= 0 or price != price:
                continue
            budget = min(config.position_size, cash)
            fee = fee_for(symbol, budget)
            quantity = round(max(budget - fee, 0) / price, 6)
            if quantity <= 0:
                continue
            targets = compute_price_targets(
                row.to_dict(), "BUY", config.horizon_days,
                target_atr_factor=config.target_atr_factor,
                stop_atr_factor=config.stop_atr_factor,
            ) or {}
            open_trades[symbol] = Trade(
                symbol=symbol, entry_date=t, entry_price=price,
                quantity=quantity, fee_buy=fee,
                target_price=targets.get("target_price"),
                stop_price=targets.get("stop_price"),
            )
            cash -= quantity * price + fee
        pending_entries = []

        # 2) Exits: Stop → Ziel → Horizont → Exit-Signal
        for symbol in list(open_trades.keys()):
            frame = frames[symbol]
            if t not in frame.index:
                continue
            trade = open_trades[symbol]
            row = frame.loc[t]
            low, high, close = float(row["low"]), float(row["high"]), float(row["close"])
            if trade.stop_price and low <= trade.stop_price:
                close_trade(trade, t, trade.stop_price * (1 - slip), "stop")
                continue
            if trade.target_price and high >= trade.target_price:
                close_trade(trade, t, trade.target_price * (1 - slip), "target")
                continue
            if (t - trade.entry_date).days >= config.horizon_days:
                close_trade(trade, t, close * (1 - slip), "horizon")
                continue
            snapshot = row.to_dict()
            if snapshot.get("sma200") == snapshot.get("sma200"):  # Warmup fertig
                if score(symbol, snapshot) <= -config.threshold:
                    close_trade(trade, t, close * (1 - slip), "signal")

        # 3) Einstiegssignale auf Schlusskurs-Basis → morgen füllen
        candidates: list[tuple[float, str]] = []
        for symbol, frame in frames.items():
            if t not in frame.index or t < first_tradeable[symbol]:
                continue
            if symbol in open_trades:
                continue
            if symbol in last_exit and (t - last_exit[symbol]).days < config.cooldown_days:
                continue
            row = frame.loc[t]
            snapshot = row.to_dict()
            if snapshot.get("sma200") != snapshot.get("sma200"):
                continue
            value = score(symbol, snapshot)
            if value >= config.threshold:
                candidates.append((value, symbol))
        candidates.sort(reverse=True)
        slots = config.max_positions - len(open_trades)
        pending_entries = [symbol for _, symbol in candidates[:max(slots, 0)]]

        # 4) Equity festhalten (Cash + offene Positionen zum letzten Close)
        position_value = sum(
            tr.quantity * last_close.get(sym, tr.entry_price)
            for sym, tr in open_trades.items()
        )
        equity_index.append(t)
        equity_values.append(cash + position_value)

    equity = pd.Series(equity_values, index=equity_index, dtype=float)
    return BacktestResult(
        config=config, equity=equity,
        trades=closed + list(open_trades.values()),
        cash_final=cash, warnings=warnings,
    )
