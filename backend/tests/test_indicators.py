"""Tests für die Indikator-Berechnung (synthetische Kursdaten)."""

import numpy as np
import pandas as pd

from app.processing.indicators import atr, compute_indicators, rsi


def make_df(closes: list[float]) -> pd.DataFrame:
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "open": c * 0.995,
        "high": c * 1.01,
        "low": c * 0.99,
        "close": c,
        "volume": np.full(len(c), 1_000_000),
    }, index=pd.date_range("2025-01-01", periods=len(c), freq="D", tz="UTC"))


def test_rsi_grenzen_und_richtung():
    steigend = make_df(list(np.linspace(100, 200, 100)))
    fallend = make_df(list(np.linspace(200, 100, 100)))
    assert rsi(steigend["close"]).iloc[-1] > 70
    assert rsi(fallend["close"]).iloc[-1] < 30
    assert rsi(steigend["close"]).between(0, 100).all()


def test_atr_positiv_und_volatilitaet():
    ruhig = make_df([100.0] * 60)
    wild = make_df([100 + (15 if i % 2 else -15) for i in range(60)])
    assert atr(wild).iloc[-1] > atr(ruhig).iloc[-1] >= 0


def test_snapshot_vollstaendig():
    df = make_df(list(np.linspace(100, 130, 250)))
    result = compute_indicators(df)
    snap = result["snapshot"]
    for key in ("close", "atr14", "rsi14", "macd_hist", "bb_upper", "bb_lower",
                "sma50", "sma200", "high_60d", "low_60d"):
        assert snap.get(key) is not None, f"{key} fehlt"
    assert snap["high_60d"] >= snap["close"] * 0.99
    assert snap["low_60d"] <= snap["close"]
    assert "rsi14" in result["series"]


def test_zu_wenig_daten_leer():
    df = make_df([100.0] * 10)
    assert compute_indicators(df) == {"series": {}, "snapshot": {}}
