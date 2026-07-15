"""eCH-0196 „E-Steuerauszug" — XML-Generierung (Schweiz).

Erzeugt ein taxStatement-XML (Namespace eCH-0196/2) mit dem
Wertschriftenverzeichnis per 31.12. und den Käufen/Verkäufen des Jahres,
in CHF umgerechnet. Bewusste Grenzen — der Auszug ist UNZERTIFIZIERT
(keine Bank/kein Finanzinstitut dahinter) und enthält keine
Dividenden/Verrechnungssteuer (die trägst du aus dem Broker-Jahresauszug
nach). Für den Import in die kantonale Steuersoftware (Private Tax ZH).

XML-Struktur nach eCH-0196 V2: taxStatement → listOfSecurities → depot →
security → taxValue (+ stock-Mutationen). Werte als Attribute.
"""

import logging
from xml.etree.ElementTree import Element, SubElement, tostring

logger = logging.getLogger(__name__)

_NS = "http://www.ech.ch/xmlns/eCH-0196/2"
# minorVersion: nach realem Import in die ZH-Software ggf. anzupassen
_MINOR_VERSION = "2"


def _country_for(currency: str | None, symbol: str) -> str | None:
    if symbol.upper().endswith(".DE"):
        return "DE"
    return {"USD": "US", "EUR": "DE", "CHF": "CH", "GBP": "GB"}.get((currency or "").upper())


def _fmt(v, digits: int = 2) -> str:
    return f"{float(v):.{digits}f}" if v is not None else "0.00"


def build_statement_id(year: int) -> str:
    """32-stellige Auszugs-ID im eCH-Muster (CH + Präfix + Datum + Zähler)."""
    prefix = "CHSTX".ljust(21, "0")[:21]  # unzertifizierte, feste Kennung
    return f"{prefix}{year}1231001"


def generate_ech0196(data: dict, taxpayer: dict) -> str:
    """data: angereicherter Steuerreport (mit CHF-Werten, ISIN je Position).
    taxpayer: {first_name, last_name, canton, tin}. Liefert XML-String."""
    year = data["year"]
    root = Element("taxStatement", {
        "xmlns": _NS,
        "id": build_statement_id(year),
        "minorVersion": _MINOR_VERSION,
        "creationDate": f"{year}-12-31T23:59:59",
        "taxPeriod": str(year),
        "periodFrom": f"{year}-01-01",
        "periodTo": f"{year}-12-31",
        "country": "CH",
        "canton": (taxpayer.get("canton") or "ZH").upper(),
    })

    inst = SubElement(root, "institution", {"name": "stx-swing-analyzer (unzertifiziert)"})
    del inst  # nur Name — keine UID (kein Finanzinstitut)

    client_attrs = {
        "clientNumber": "1",
        "firstName": taxpayer.get("first_name") or "—",
        "lastName": taxpayer.get("last_name") or "—",
    }
    if taxpayer.get("tin"):
        client_attrs["tin"] = taxpayer["tin"]
    SubElement(root, "client", client_attrs)

    holdings = data["holdings"]
    trades_by_symbol: dict[str, list] = {}
    for t in data["trades"]:
        trades_by_symbol.setdefault(t["symbol"], []).append(t)

    total_tax_value = round(sum(h.get("chf_value") or 0 for h in holdings), 2)
    los = SubElement(root, "listOfSecurities", {
        "totalTaxValue": _fmt(total_tax_value),
        "totalGrossRevenueA": "0.00",
        "totalGrossRevenueB": "0.00",
        "totalWithHoldingTaxClaim": "0.00",
    })
    depot = SubElement(los, "depot", {"depotNumber": "IBKR"})

    for i, h in enumerate(holdings, start=1):
        is_fund = h.get("asset_type") == "etf"
        sec_attrs = {
            "positionId": str(i),
            "securityName": h.get("name") or h["symbol"],
            "currency": h.get("currency") or "USD",
            "quotationType": "PIECE",
            "securityCategory": "FUND" if is_fund else "SHARE",
            "securityType": "FUND.ACCUMULATION" if is_fund else "SHARE.COMMON",
        }
        if h.get("isin"):
            sec_attrs["isin"] = h["isin"]
        country = _country_for(h.get("currency"), h["symbol"])
        if country:
            sec_attrs["country"] = country
        security = SubElement(depot, "security", sec_attrs)

        rate = h.get("fx_rate") or 1.0
        SubElement(security, "taxValue", {
            "referenceDate": f"{year}-12-31",
            "quotationType": "PIECE",
            "quantity": _fmt(h["quantity"], 4),
            "balanceCurrency": h.get("currency") or "USD",
            "unitPrice": _fmt(h.get("year_end_close"), 4),
            "balance": _fmt(h.get("year_end_value")),
            "exchangeRate": _fmt(rate, 6),
            "value": _fmt(h.get("chf_value")),
        })

        # Käufe/Verkäufe des Jahres als Bestandsmutationen
        for t in trades_by_symbol.get(h["symbol"], []):
            SubElement(security, "stock", {
                "referenceDate": t["exit_date"],
                "mutation": "true",
                "name": "Verkauf",
                "quotationType": "PIECE",
                "quantity": _fmt(-t["quantity"], 4),
                "balanceCurrency": t.get("currency") or "USD",
                "unitPrice": _fmt(t["exit_price"], 4),
                "value": _fmt(t["proceeds"]),
            })

    xml = tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml
