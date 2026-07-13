"""Kennzahlen aus Equity-Kurve und Trade-Liste."""

import math

import pandas as pd

from app.backtest.engine import BacktestResult

TRADING_DAYS_PER_YEAR = 252


def compute_metrics(result: BacktestResult) -> dict:
    equity = result.equity
    trades = [t for t in result.trades if t.exit_price is not None]
    start = result.config.start_capital

    out: dict = {
        "num_trades": len(trades),
        "open_positions": len(result.trades) - len(trades),
    }
    if equity.empty:
        return {**out, "total_return_pct": 0.0}

    final = float(equity.iloc[-1])
    out["final_equity"] = round(final, 2)
    out["total_return_pct"] = round((final / start - 1) * 100, 2)

    # CAGR über die tatsächliche Laufzeit
    days = max((equity.index[-1] - equity.index[0]).days, 1)
    years = days / 365.25
    if years > 0.1 and final > 0:
        out["cagr_pct"] = round(((final / start) ** (1 / years) - 1) * 100, 2)

    # Max Drawdown
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max
    out["max_drawdown_pct"] = round(float(drawdown.min()) * 100, 2)

    # Sharpe (daily, rf=0, annualisiert)
    returns = equity.pct_change().dropna()
    if len(returns) > 10 and float(returns.std()) > 0:
        out["sharpe"] = round(
            float(returns.mean()) / float(returns.std()) * math.sqrt(TRADING_DAYS_PER_YEAR), 2)

    # Trade-Statistik
    if trades:
        pnls = [t.pnl for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        out["win_rate"] = round(len(wins) / len(pnls), 3)
        out["avg_win"] = round(sum(wins) / len(wins), 2) if wins else 0.0
        out["avg_loss"] = round(sum(losses) / len(losses), 2) if losses else 0.0
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        out["profit_factor"] = round(gross_win / gross_loss, 2) if gross_loss > 0 else None
        out["fees_total"] = round(sum(t.fee_buy + t.fee_sell for t in result.trades), 2)
        out["exit_reasons"] = {
            reason: sum(1 for t in trades if t.reason == reason)
            for reason in ("target", "stop", "horizon", "signal")
        }
    return out


def buy_and_hold_return(df: pd.DataFrame, start_ts, end_ts) -> float | None:
    """Benchmark: Buy&Hold-Rendite in % über denselben Zeitraum."""
    window = df.loc[(df.index >= start_ts) & (df.index <= end_ts), "close"]
    if len(window) < 2:
        return None
    return round((float(window.iloc[-1]) / float(window.iloc[0]) - 1) * 100, 2)
