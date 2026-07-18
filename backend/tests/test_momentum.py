"""Tests für die Momentum-Strategie: Trailing-Stop, Regime-Filter, Scoring."""

import numpy as np
import pandas as pd

from app.analysis.scoring import momentum_score
from app.backtest.engine import run_backtest
from app.backtest.params import StrategyConfig


def make_df(closes, volume=1_000_000) -> pd.DataFrame:
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
        "volume": np.full(len(c), volume),
    }, index=pd.date_range("2020-01-01", periods=len(c), freq="B"))


CFG_MOM = StrategyConfig(
    start_capital=10_000, position_size=1_000, max_positions=10,
    slippage_bps=0.0, warmup_days=210, horizon_days=500,
    cooldown_days=3, threshold=0.35,
    strategy_kind="momentum", trailing_stop_atr=2.0, stop_atr_factor=1.5,
)


def test_trailing_stop_laesst_gewinner_laufen():
    """Rally 100→130, dann Absturz: Exit nahe Hochwasser − 2×ATR, mit Gewinn."""
    closes = [100.0] * 250 + list(np.linspace(100, 130, 25)) + \
             list(np.linspace(130, 100, 15))
    df = make_df(closes)

    # Signal genau einmal feuern (erster handelbarer Tag)
    fired = {"done": False}

    def score_once(symbol, snapshot):
        if not fired["done"]:
            fired["done"] = True
            return 1.0
        return 0.0

    result = run_backtest({"TST": df}, CFG_MOM, score_fn=score_once)
    trades = [t for t in result.trades if t.exit_date is not None]
    assert len(trades) == 1
    trade = trades[0]
    assert trade.reason == "stop"
    # Kein Fixziel bei Trailing — der Gewinner durfte laufen
    assert trade.target_price is None
    # Exit deutlich über dem Einstieg (Rally mitgenommen, ~130 − 2×ATR)
    assert trade.exit_price > trade.entry_price * 1.15
    assert trade.pnl > 0


def test_trailing_stop_wird_nie_gesenkt():
    """Nach der Rally bleibt der Stop am Hochwasser verankert."""
    closes = [100.0] * 250 + list(np.linspace(100, 120, 20)) + [120.0] * 30
    df = make_df(closes)
    fired = {"done": False}

    def score_once(symbol, snapshot):
        if not fired["done"]:
            fired["done"] = True
            return 1.0
        return 0.0

    result = run_backtest({"TST": df}, CFG_MOM, score_fn=score_once)
    open_trades = [t for t in result.trades if t.exit_date is None]
    assert len(open_trades) == 1
    # Stop liegt unter dem Hochwasser (~121.2 High), aber weit über dem Einstieg
    assert open_trades[0].stop_price > open_trades[0].entry_price


def test_regime_filter_blockt_abwaertstrend():
    """Score sagt kaufen, aber Kurs unter SMA200 → Regime-Filter blockt."""
    closes = list(np.linspace(200, 100, 300))  # stetiger Abwärtstrend
    df = make_df(closes)
    cfg = StrategyConfig(
        start_capital=10_000, position_size=1_000, max_positions=10,
        slippage_bps=0.0, warmup_days=210, horizon_days=30,
        cooldown_days=3, threshold=0.35, regime_sma=200,
    )
    result = run_backtest({"TST": df}, cfg, score_fn=lambda s, snap: 1.0)
    assert len(result.trades) == 0


def test_momentum_score_profile():
    up = {"close": 100, "sma50": 95, "sma200": 90, "rsi14": 55,
          "macd_hist": 0.5, "macd_hist_prev": 0.4, "high_60d": 101}
    score_up, comps = momentum_score(up)
    # Aufwärtstrend + Breakout-Nähe + gesunder Pullback → klar über Schwelle
    assert score_up >= 0.5
    assert comps["trend"] == 0.4 and comps["pullback"] == 0.2

    down = {"close": 80, "sma50": 90, "sma200": 95, "rsi14": 45,
            "macd_hist": -0.5, "macd_hist_prev": -0.4, "high_60d": 100}
    score_down, comps_down = momentum_score(down)
    # Trendbruch → deutlich negativ (löst Signal-Exit aus)
    assert score_down <= -0.4

    hot = {"close": 100, "sma50": 95, "sma200": 90, "rsi14": 85,
           "macd_hist": 0.5, "macd_hist_prev": 0.6, "high_60d": 100}
    _, comps_hot = momentum_score(hot)
    assert comps_hot["pullback"] == -0.2  # überhitzt wird bestraft
