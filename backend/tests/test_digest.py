"""Tests für die tägliche Handelsempfehlung (reine Render-/Regel-Logik)."""

from app.analysis.recommendations import render_digest


def test_render_digest_vollstaendig():
    text = render_digest({
        "reference_portfolio": "SwissQuote_Depot1", "portfolio_value": 70000,
        "buys": [{"symbol": "NVDA", "confidence": 0.8, "price": 210.0,
                  "target": 223.9, "stop": 198.9, "crv": 1.33, "source": "Signal",
                  "sizing": {"quantity": 63, "volume": 13230.0}}],
        "screener_buys": [{"symbol": "SCCO", "score": 0.44, "price": 182.5,
                           "target": 195.0, "stop": 175.0, "crv": 1.6,
                           "source": "Screener", "sizing": None}],
        "reviews": [
            {"portfolio": "Depot", "symbol": "AAPL", "quantity": 10, "entry": 200.0,
             "price": 190.0, "pnl_pct": -5.0, "target": 230.0, "stop": 192.0,
             "verdict": "VERKAUFEN", "reason": "Stop 192.0 erreicht"},
            {"portfolio": "Depot", "symbol": "MSFT", "quantity": 5, "entry": 400.0,
             "price": 420.0, "pnl_pct": 5.0, "target": 450.0, "stop": 380.0,
             "verdict": "HALTEN", "reason": ""},
        ],
    })
    assert "NVDA" in text and "63 Stk." in text
    assert "🔴 VERKAUFEN: AAPL" in text and "Stop 192.0 erreicht" in text
    assert "⚪ HALTEN: MSFT" in text
    assert "1 verkaufen, 0 prüfen, 1 halten" in text


def test_render_digest_leer():
    text = render_digest({"reference_portfolio": None, "portfolio_value": None,
                          "buys": [], "screener_buys": [], "reviews": []})
    assert "keine frischen BUY-Signale" in text
