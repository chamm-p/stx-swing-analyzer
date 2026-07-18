"""Tests für die Dual-Timeframe-Trendfolge (DTT): Signal, R-Ziel, Break-even."""

import numpy as np
import pandas as pd

from app.analysis.scoring import dtt_score
from app.backtest.engine import run_backtest
from app.backtest.params import StrategyConfig


def make_df(closes, highs=None, lows=None) -> pd.DataFrame:
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "open": c,
        "high": np.asarray(highs, dtype=float) if highs is not None else c * 1.005,
        "low": np.asarray(lows, dtype=float) if lows is not None else c * 0.995,
        "close": c, "volume": np.full(len(c), 1_000_000),
    }, index=pd.date_range("2019-01-01", periods=len(c), freq="B"))


def test_dtt_signal_bedingungen():
    # Alle Bedingungen erfüllt → Einstieg
    ok = {"close": 110, "ema200": 100, "sma20": 105, "sma50": 104,
          "sma20_prev": 103, "sma50_prev": 104, "rsi14": 60}
    assert dtt_score(ok)[0] == 1.0

    # Kein frischer Cross (SMA20 war schon oben) → kein Signal
    no_cross = {**ok, "sma20_prev": 105}
    assert dtt_score(no_cross)[0] == 0.0

    # RSI überkauft → kein Signal
    assert dtt_score({**ok, "rsi14": 75})[0] == 0.0
    # RSI unter 50 → kein Signal
    assert dtt_score({**ok, "rsi14": 45})[0] == 0.0

    # Unter EMA200 → Trendbruch-Exit
    assert dtt_score({**ok, "close": 95})[0] == -1.0
    # SMA20 unter SMA50 → Trendbruch-Exit
    assert dtt_score({**ok, "sma20": 103, "sma50": 104})[0] == -1.0


def test_dtt_fixziel_ist_crv_2():
    """Aufwärtstrend mit Golden Cross → Ziel = Einstieg + 2×(Einstieg−Stop)."""
    # Langer Aufbau über EMA200, dann ein SMA20/50-Cross, dann Rally ins Ziel
    base = list(np.linspace(80, 100, 220))          # Aufbau, Kurs über EMA
    ramp = list(np.linspace(100, 160, 60))          # Rally nach dem Cross
    df = make_df(base + ramp)
    cfg = StrategyConfig(
        start_capital=10_000, position_size=1_000, max_positions=5,
        slippage_bps=0.0, warmup_days=200, horizon_days=365,
        cooldown_days=3, threshold=0.5,
        strategy_kind="dtt", target_r=2.0, breakeven_r=0.0,
    )
    result = run_backtest({"TST": df}, cfg)
    done = [t for t in result.trades if t.exit_date is not None]
    assert done, "kein Trade ausgelöst"
    tp = [t for t in done if t.reason == "target"]
    assert tp, "Ziel wurde nicht erreicht"
    tr = tp[0]
    expected_target = tr.entry_price + 2.0 * tr.risk_unit
    assert abs(tr.exit_price - expected_target) / expected_target < 0.02
    assert tr.pnl > 0


def test_dtt_breakeven_schuetzt_gewinn():
    """Rally auf 1:1, dann Absturz unter Einstieg → Break-even-Stop = Einstieg."""
    base = list(np.linspace(80, 100, 220))
    up = list(np.linspace(100, 112, 12))   # über 1:1 (Stop ~5% → 1R ~5)
    down = list(np.linspace(112, 90, 20))  # zurück unter Einstieg
    df = make_df(base + up + down)
    cfg = StrategyConfig(
        start_capital=10_000, position_size=1_000, max_positions=5,
        slippage_bps=0.0, warmup_days=200, horizon_days=365,
        cooldown_days=3, threshold=0.5,
        strategy_kind="dtt", target_r=5.0, breakeven_r=1.0,  # Ziel weit → BE greift zuerst
    )
    result = run_backtest({"TST": df}, cfg)
    done = [t for t in result.trades if t.exit_date is not None]
    assert done
    tr = done[0]
    # Break-even: Exit ~ Einstieg (nicht der ursprüngliche Swing-Low-Stop)
    assert tr.exit_price >= tr.entry_price * 0.995
    assert tr.reason == "stop"
