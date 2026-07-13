"""Tests für Kursziel-/Stop-Berechnung (ATR-Zielzone)."""

from app.analysis.targets import compute_price_targets


def snap(close=100.0, atr=4.0, high60=200.0, low60=50.0) -> dict:
    return {"close": close, "atr14": atr, "high_60d": high60, "low_60d": low60}


def test_buy_atr_projektion():
    # Horizont 14d: Ziel = close + 2*ATR, Stop = close - 1.5*ATR
    t = compute_price_targets(snap(), "BUY", 14)
    assert t["target_price"] == 108.0
    assert t["stop_price"] == 94.0
    assert t["risk_reward"] == round(8.0 / 6.0, 2)


def test_buy_deckelung_am_swing_high():
    # Swing-High (105) liegt zwischen close+0.5*ATR (102) und Rohziel (108)
    t = compute_price_targets(snap(high60=105.0), "BUY", 14)
    assert t["target_price"] == 105.0


def test_buy_breakout_ohne_deckelung():
    # Kurs notiert quasi am Hoch → ATR-Projektion greift
    t = compute_price_targets(snap(high60=100.5), "BUY", 14)
    assert t["target_price"] == 108.0


def test_sell_spiegelbildlich():
    t = compute_price_targets(snap(), "SELL", 14)
    assert t["target_price"] == 92.0
    assert t["stop_price"] == 106.0


def test_sell_deckelung_am_swing_low():
    t = compute_price_targets(snap(low60=95.0), "SELL", 14)
    assert t["target_price"] == 95.0


def test_horizont_skaliert_ziel():
    kurz = compute_price_targets(snap(), "BUY", 7)
    lang = compute_price_targets(snap(), "BUY", 28)
    assert kurz["target_price"] < lang["target_price"]
    # Stop hängt nicht vom Horizont ab
    assert kurz["stop_price"] == lang["stop_price"]


def test_hold_und_fehlende_daten_ergeben_none():
    assert compute_price_targets(snap(), "HOLD", 14) is None
    assert compute_price_targets({"close": 100.0}, "BUY", 14) is None
    assert compute_price_targets({"close": None, "atr14": 4.0}, "BUY", 14) is None
