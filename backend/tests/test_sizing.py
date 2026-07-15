"""Tests für die goldenen Swing-Regeln (1%-Regel, CRV-Guard)."""

from app.analysis.position_sizing import crv, risk_based_quantity


def test_1_prozent_regel():
    # 10'000 Portfolio, Kurs 100, Stop 95 → Risiko 100 / 5 = 20 Stück
    assert risk_based_quantity(10_000, 100.0, 95.0, 1.0) == 20.0
    # halbes Risiko → halbe Stückzahl
    assert risk_based_quantity(10_000, 100.0, 95.0, 0.5) == 10.0
    # engerer Stop erlaubt mehr Stücke
    assert risk_based_quantity(10_000, 100.0, 98.0, 1.0) == 50.0


def test_1_prozent_regel_ohne_validen_stop():
    assert risk_based_quantity(10_000, 100.0, None, 1.0) is None
    assert risk_based_quantity(10_000, 100.0, 100.0, 1.0) is None  # Stop == Kurs
    assert risk_based_quantity(10_000, 100.0, 105.0, 1.0) is None  # Stop über Kurs
    assert risk_based_quantity(0, 100.0, 95.0, 1.0) is None


def test_crv():
    assert crv(100.0, 110.0, 95.0) == 2.0     # 10 Chance / 5 Risiko
    assert crv(100.0, 105.0, 95.0) == 1.0
    assert crv(100.0, None, 95.0) is None
    assert crv(100.0, 110.0, None) is None
    assert crv(100.0, 95.0, 90.0) is None     # Ziel unter Kurs
