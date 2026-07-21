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


# ------------------------------------------------------- IBKR-Symbol-Mapping

def test_ibkr_yahoo_symbol_mapping():
    from app.broker.ibkr_sync import yahoo_symbol

    assert yahoo_symbol("AAPL", "USD", "STK") == "AAPL"
    assert yahoo_symbol("BRK B", "USD", "STK") == "BRK-B"
    assert yahoo_symbol("SAP", "EUR", "STK") == "SAP.DE"
    assert yahoo_symbol("AAPL", "USD", "OPT") is None  # keine Optionen
    assert yahoo_symbol("NESN", "CHF", "STK") is None  # SIX nicht gemappt


def test_dh_prime_parser(tmp_path):
    """dhparam.pem (ASN.1 SEQUENCE {prime, generator}) → Hex-Prime."""
    import base64

    from app.broker.ibkr import dh_prime_from_pem

    prime = (1 << 255) + 977  # irgendein grosser Wert mit fuehrendem High-Bit-Byte
    pbytes = prime.to_bytes(33, "big")  # 0x00-Padding wie DER es verlangt
    inner = b"\x02" + bytes([len(pbytes)]) + pbytes + b"\x02\x01\x02"
    der = b"\x30" + bytes([len(inner)]) + inner
    pem = ("-----BEGIN DH PARAMETERS-----\n"
           + base64.b64encode(der).decode()
           + "\n-----END DH PARAMETERS-----\n")
    f = tmp_path / "dhparam.pem"
    f.write_text(pem)
    assert dh_prime_from_pem(str(f)) == format(prime, "x")


def test_challenger_umgeht_crv_guard():
    """Challenger-Kandidaten (origin=strategy) sind vom globalen CRV-Guard
    ausgenommen; Screener-Kandidaten nicht."""
    from app.analysis.position_sizing import crv

    # ATR-Geometrie 2.0/1.5 → CRV 1.33 < globaler min_crv 1.5
    price, target, stop = 100.0, 102.0, 98.5
    c = crv(price, target, stop)
    assert c is not None and c < 1.5
    # Der Guard blockiert nur, wenn origin != "strategy" — die
    # Ausnahmelogik ist in _run_entries; hier sichern wir die Geometrie ab,
    # die den Konflikt erzeugt (2×ATR Ziel / 1.5×ATR Stop = 1.33).
    assert round(c, 2) == 1.33
