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
