"""Tests für den eCH-0196-Steuerauszug (Struktur + Wohlgeformtheit)."""

from xml.etree.ElementTree import fromstring

from app.reports.ech0196 import build_statement_id, generate_ech0196

_NS = "{http://www.ech.ch/xmlns/eCH-0196/2}"

SAMPLE = {
    "year": 2025,
    "holdings": [
        {"symbol": "AAPL", "name": "Apple Inc.", "asset_type": "stock",
         "currency": "USD", "quantity": 100, "year_end_close": 245.5,
         "year_end_value": 24550.0, "isin": "US0378331005",
         "fx_rate": 0.885, "chf_value": 21726.75},
        {"symbol": "SAP.DE", "name": "SAP SE", "asset_type": "stock",
         "currency": "EUR", "quantity": 50, "year_end_close": 240.0,
         "year_end_value": 12000.0, "isin": None,
         "fx_rate": 0.93, "chf_value": 11160.0},
    ],
    "trades": [
        {"symbol": "AAPL", "currency": "USD", "quantity": 20,
         "exit_date": "2025-06-15", "exit_price": 250.0, "proceeds": 5000.0},
    ],
}
TAXPAYER = {"first_name": "Max", "last_name": "Muster", "canton": "ZH", "tin": ""}


def test_wohlgeformt_und_namespace():
    xml = generate_ech0196(SAMPLE, TAXPAYER)
    root = fromstring(xml.split("?>", 1)[1])
    assert root.tag == _NS + "taxStatement"
    assert root.attrib["taxPeriod"] == "2025"
    assert root.attrib["canton"] == "ZH"


def test_wertschriften_und_totals():
    xml = generate_ech0196(SAMPLE, TAXPAYER)
    root = fromstring(xml.split("?>", 1)[1])
    los = root.find(_NS + "listOfSecurities")
    # Total = Summe der CHF-Werte
    assert los.attrib["totalTaxValue"] == "32886.75"
    securities = los.find(_NS + "depot").findall(_NS + "security")
    assert len(securities) == 2
    aapl = securities[0]
    assert aapl.attrib["isin"] == "US0378331005"
    assert aapl.attrib["securityCategory"] == "SHARE"
    tv = aapl.find(_NS + "taxValue")
    assert tv.attrib["value"] == "21726.75"
    assert tv.attrib["balanceCurrency"] == "USD"
    # Verkauf als Bestandsmutation
    stock = aapl.find(_NS + "stock")
    assert stock is not None and stock.attrib["name"] == "Verkauf"


def test_client_und_id():
    xml = generate_ech0196(SAMPLE, TAXPAYER)
    root = fromstring(xml.split("?>", 1)[1])
    client = root.find(_NS + "client")
    assert client.attrib["lastName"] == "Muster"
    assert "tin" not in client.attrib  # leer → weggelassen
    assert len(build_statement_id(2025)) == 32
