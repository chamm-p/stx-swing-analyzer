"""Tests für Discovery-Scan-Bausteine und Index-Refresh-Parsing —
alles reine Funktionen ohne Netz/DB."""

import numpy as np
import pandas as pd

from app.analysis.discovery import _extract_symbol, _score_frame
from app.analysis.scoring import effective_threshold, get_profile
from app.backtest.runner import recommendation_from
from app.sources.exchange_dirs import _parse_nasdaq_file
from app.sources.indices import _extract, _normalize


def make_df(n=260, price=50.0, volume=200_000, trend=0.0):
    closes = price + np.arange(n) * trend
    return pd.DataFrame({
        "open": closes, "high": closes * 1.01, "low": closes * 0.99,
        "close": closes, "volume": np.full(n, volume),
    }, index=pd.date_range("2024-01-01", periods=n, freq="B"))


PROFILE = get_profile("stock")
THRESHOLD = effective_threshold(PROFILE)


# ------------------------------------------------------------ Discovery-Filter

def test_score_frame_liefert_kandidat_mit_deltas():
    df = make_df(trend=0.1)
    row = _score_frame(df, PROFILE, THRESHOLD)
    assert row is not None
    assert row["action"] in ("BUY", "SELL", "HOLD")
    assert row["change_1d"] is not None
    assert row["avg_turnover"] > 500_000


def test_score_frame_filtert_pennystocks():
    assert _score_frame(make_df(price=1.5), PROFILE, THRESHOLD) is None


def test_score_frame_filtert_illiquide():
    # 50 × 5000 = 250k Tagesumsatz < 500k-Default
    assert _score_frame(make_df(volume=5_000), PROFILE, THRESHOLD) is None


def test_score_frame_braucht_genug_historie():
    assert _score_frame(make_df(n=60), PROFILE, THRESHOLD) is None


def test_extract_symbol_multiindex():
    df = make_df(n=150)
    bulk = pd.concat({"AAPL": df.rename(columns=str.capitalize)}, axis=1)
    sub = _extract_symbol(bulk, "AAPL")
    assert sub is not None and "close" in sub.columns
    assert _extract_symbol(bulk, "MSFT") is None


# ------------------------------------------------------- NASDAQ-Verzeichnis

NASDAQ_SAMPLE = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N
ZTEST|Test Issue Inc.|Q|Y|N|100|N|N
QQQ|Invesco QQQ Trust|G|N|N|100|Y|N
ABCW|ABC Corp - Warrant|Q|N|N|100|N|N
File Creation Time: 0715202622:01|||||||"""

OTHER_SAMPLE = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
BRK.B|Berkshire Hathaway Class B|N|BRK B|N|100|N|BRK=B
SPY|SPDR S&P 500 ETF|P|SPY|Y|100|N|SPY
GME|GameStop Corp|N|GME|N|100|N|GME
ABC$P|ABC Corp Preferred|N|ABC$P|N|100|N|ABC-P"""


def test_parse_nasdaq_filtert_tests_etfs_warrants():
    rows = _parse_nasdaq_file(NASDAQ_SAMPLE, "Symbol", is_other=False)
    assert rows == [["AAPL", "Apple Inc. - Common Stock"]]


def test_parse_other_yahoo_notation_und_exchange_filter():
    rows = _parse_nasdaq_file(OTHER_SAMPLE, "ACT Symbol", is_other=True)
    symbols = [r[0] for r in rows]
    assert "BRK-B" in symbols  # Klassen-Suffix in Yahoo-Notation
    assert "GME" in symbols
    assert "SPY" not in symbols  # Arca (P) ausgefiltert
    assert all("$" not in s for s in symbols)


# ------------------------------------------------------------ Index-Refresh

def test_normalize_ticker_stile():
    assert _normalize("BRK.B", "us") == "BRK-B"
    assert _normalize("ADS.DE[1]", ".DE") == "ADS.DE"
    assert _normalize("BMW", ".DE") == "BMW.DE"
    assert _normalize("ASML.AS", "keep") == "ASML.AS"
    assert _normalize("", "us") is None


def test_extract_findet_beste_tabelle():
    html = """
    <table><tr><th>Jahr</th><th>Wert</th></tr><tr><td>2024</td><td>1</td></tr></table>
    <table>
      <tr><th>Ticker</th><th>Company</th></tr>
      <tr><td>AAPL</td><td>Apple</td></tr>
      <tr><td>BRK.B</td><td>Berkshire</td></tr>
    </table>"""
    members = _extract(html, "us")
    assert members == {"AAPL": "Apple", "BRK-B": "Berkshire"}


# --------------------------------------------------------- Empfehlungs-Verdict

def test_recommendation_no_trade_bei_negativem_oos():
    reco = recommendation_from({
        "total_return_pct": -4.61, "windows_tested": 14, "windows_flat": 19,
        "param_wins": {"{'threshold': 0.4}": 5},
    })
    assert reco["verdict"] == "no_trade"
    assert "nicht" in reco["reason"]


def test_recommendation_params_bei_positivem_oos():
    reco = recommendation_from({
        "total_return_pct": 34.0, "windows_tested": 30,
        "param_wins": {"{'threshold': 0.35}": 4, "{'threshold': 0.3}": 3},
    })
    assert reco["verdict"] == "params"
    assert reco["params"] == {"threshold": 0.35}
    assert reco["share"] == 0.13
