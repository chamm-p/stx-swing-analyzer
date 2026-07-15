"""Tests für die Gebühren-Staffel (Swissquote-Vorgabe des Users)."""

from app.analysis.fees import _SWISSQUOTE_DEFAULT, _SWISSQUOTE_EUR, compute_fee

FEES = {"default": _SWISSQUOTE_DEFAULT, "EUR": _SWISSQUOTE_EUR}


def test_staffel_grenzen_default():
    assert compute_fee(FEES, "USD", 0.01) == 3.0
    assert compute_fee(FEES, "USD", 500.00) == 3.0
    assert compute_fee(FEES, "USD", 500.01) == 5.0
    assert compute_fee(FEES, "CHF", 1000.00) == 5.0
    assert compute_fee(FEES, "CHF", 1000.01) == 10.0
    assert compute_fee(FEES, "USD", 2000.01) == 29.0
    assert compute_fee(FEES, "USD", 10000.01) == 49.0
    assert compute_fee(FEES, "USD", 15000.01) == 79.0
    assert compute_fee(FEES, "USD", 25000.01) == 129.0
    assert compute_fee(FEES, "USD", 50000.01) == 190.0
    assert compute_fee(FEES, "USD", 1_000_000) == 190.0


def test_eur_erste_stufe_abweichend():
    assert compute_fee(FEES, "EUR", 300) == 5.0     # statt 3
    assert compute_fee(FEES, "EUR", 700) == 5.0
    assert compute_fee(FEES, "EUR", 1500) == 10.0   # ab hier identisch


def test_unbekannte_waehrung_nutzt_default():
    assert compute_fee(FEES, "GBP", 300) == 3.0
    assert compute_fee(FEES, None, 300) == 3.0


def test_ohne_staffel_keine_gebuehr():
    assert compute_fee(None, "USD", 1000) == 0.0
    assert compute_fee({}, "USD", 1000) == 0.0
    assert compute_fee(FEES, "USD", 0) == 0.0


# ------------------------------------------------------------ IBKR-Modelle

from app.analysis.fees import _IBKR_FIXED, _IBKR_TIERED  # noqa: E402


def test_ibkr_fixed_us_per_share():
    # 100 Aktien à $50: 100 × 0.005 = $0.50 → Minimum $1 greift
    assert compute_fee(_IBKR_FIXED, "USD", 5000, quantity=100) == 1.0
    # 1000 Aktien à $50: 1000 × 0.005 = $5
    assert compute_fee(_IBKR_FIXED, "USD", 50000, quantity=1000) == 5.0
    # Penny-Stock: 50000 Aktien à $0.02 (Volumen 1000): 250 > 1% → Deckel $10
    assert compute_fee(_IBKR_FIXED, "USD", 1000, quantity=50000) == 10.0


def test_ibkr_fixed_eur_prozent():
    # 0.05% von 10'000 = 5.00
    assert compute_fee(_IBKR_FIXED, "EUR", 10000, quantity=100) == 5.0
    # Minimum €1.25 bei kleinen Orders
    assert compute_fee(_IBKR_FIXED, "EUR", 1000, quantity=10) == 1.25
    # Deckel €29 bei großen Orders (0.05% von 100k wären 50)
    assert compute_fee(_IBKR_FIXED, "EUR", 100000, quantity=500) == 29.0


def test_ibkr_tiered_us():
    # 100 Aktien: 0.35 Minimum; 1000 Aktien: 3.50; Deckel 0.5%
    assert compute_fee(_IBKR_TIERED, "USD", 5000, quantity=100) == 0.35
    assert compute_fee(_IBKR_TIERED, "USD", 50000, quantity=1000) == 3.5


def test_per_share_ohne_stueckzahl_greift_minimum():
    assert compute_fee(_IBKR_FIXED, "USD", 5000) == 1.0
