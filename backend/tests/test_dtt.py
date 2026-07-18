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


def _fire_once():
    """score_fn, das genau einmal einen Einstieg auslöst (Signal-Logik
    ist separat getestet; hier zählt die Engine-Exit-Mechanik)."""
    state = {"done": False}

    def fn(symbol, snapshot):
        if not state["done"]:
            state["done"] = True
            return 1.0
        return 0.0
    return fn


def test_dtt_fixziel_ist_crv_2():
    """R-Fixziel: Ziel = Einstieg + 2×(Einstieg − Swing-Low-Stop)."""
    flat = [100.0] * 230
    rally = list(np.linspace(100, 106, 20))  # deckt Ziel (~101) locker
    df = make_df(flat + rally)
    cfg = StrategyConfig(
        start_capital=10_000, position_size=1_000, max_positions=5,
        slippage_bps=0.0, warmup_days=210, horizon_days=365,
        cooldown_days=3, threshold=0.5,
        strategy_kind="dtt", target_r=2.0, breakeven_r=0.0,
    )
    result = run_backtest({"TST": df}, cfg, score_fn=_fire_once())
    done = [t for t in result.trades if t.exit_date is not None]
    assert done, "kein Trade ausgelöst"
    tr = done[0]
    assert tr.reason == "target"
    expected_target = tr.entry_price + 2.0 * tr.risk_unit
    assert abs(tr.exit_price - expected_target) / expected_target < 0.02
    assert tr.pnl > 0


def test_dtt_breakeven_schuetzt_gewinn():
    """Break-even: Rally über 1:1 zieht Stop auf Einstieg; späterer Absturz
    stoppt bei Einstieg statt am ursprünglichen Swing-Low."""
    flat = [100.0] * 230
    up = list(np.linspace(100, 103, 10))   # über 1:1 (R≈0.5 → 1R bei ~100.5)
    down = list(np.linspace(103, 96, 15))  # zurück unter Einstieg
    df = make_df(flat + up + down)
    cfg = StrategyConfig(
        start_capital=10_000, position_size=1_000, max_positions=5,
        slippage_bps=0.0, warmup_days=210, horizon_days=365,
        cooldown_days=3, threshold=0.5,
        strategy_kind="dtt", target_r=10.0, breakeven_r=1.0,  # Ziel weit → BE greift zuerst
    )
    result = run_backtest({"TST": df}, cfg, score_fn=_fire_once())
    done = [t for t in result.trades if t.exit_date is not None]
    assert done
    tr = done[0]
    assert tr.reason == "stop"
    # Break-even: Exit ≈ Einstieg, nicht der ursprüngliche Swing-Low-Stop
    assert tr.exit_price >= tr.entry_price * 0.99
    assert tr.pnl > -1.0  # praktisch kein Verlust
