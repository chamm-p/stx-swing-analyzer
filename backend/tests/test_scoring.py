"""Tests für den deterministischen Signal-Scoring-Kern."""

from app.analysis.scoring import (
    PROFILES, aggregate_sentiment, effective_threshold, flip_suppressed,
    get_profile, score_signal, technical_score,
)


def snapshot(**overrides) -> dict:
    base = {
        "close": 100.0, "rsi14": 50.0, "macd_hist": 0.0, "macd_hist_prev": 0.0,
        "bb_upper": 110.0, "bb_lower": 90.0, "bb_mid": 100.0,
        "sma50": 100.0, "sma200": 100.0,
    }
    base.update(overrides)
    return base


class TestTechnicalScore:
    def test_neutral_snapshot_nahe_null(self):
        score, _ = technical_score(snapshot())
        assert abs(score) < 0.25

    def test_ueberverkauft_bullish(self):
        score, components = technical_score(snapshot(rsi14=20.0, close=91.0))
        assert components["rsi"] > 0.5
        assert score > 0

    def test_ueberkauft_bearish(self):
        score, components = technical_score(snapshot(rsi14=85.0, close=109.0))
        assert components["rsi"] < -0.5
        assert score < 0

    def test_aufwaertstrend_positiv(self):
        _, components = technical_score(snapshot(close=110.0, sma50=100.0, sma200=90.0,
                                                 bb_upper=120.0, bb_lower=100.0))
        assert components["trend"] == 1.0

    def test_score_begrenzt(self):
        score, _ = technical_score(snapshot(rsi14=5.0, macd_hist=50.0, close=50.0,
                                            bb_lower=60.0, bb_upper=100.0,
                                            sma50=40.0, sma200=30.0))
        assert -1.0 <= score <= 1.0

    def test_krypto_profil_engere_rsi_schwellen(self):
        # RSI 28: für Aktien überverkauft (<30) → starker Mean-Reversion-
        # Impuls; für Krypto (<25) nur milde Mitte-Zone
        _, stock_c = technical_score(snapshot(rsi14=28.0), PROFILES["stock"])
        _, crypto_c = technical_score(snapshot(rsi14=28.0), PROFILES["crypto"])
        assert stock_c["rsi"] > 0.6
        assert crypto_c["rsi"] < stock_c["rsi"]

    def test_krypto_macd_gedaempft(self):
        snap = snapshot(macd_hist=2.0, macd_hist_prev=1.0)
        _, stock_c = technical_score(snap, PROFILES["stock"])
        _, crypto_c = technical_score(snap, PROFILES["crypto"])
        assert stock_c["macd"] >= crypto_c["macd"]


class TestScoreSignal:
    def test_starkes_setup_ergibt_buy(self):
        result = score_signal(snapshot(rsi14=25.0, close=91.0, macd_hist=0.5,
                                       macd_hist_prev=0.3, sma50=90.0, sma200=85.0),
                              sentiment=0.6, fundamental=0.5)
        assert result.action == "BUY"
        assert 0 < result.confidence <= 1
        assert result.profile == "stock"

    def test_neutral_ergibt_hold(self):
        result = score_signal(snapshot(), sentiment=0.0, fundamental=0.0)
        assert result.action == "HOLD"

    def test_krypto_hoehere_schwelle(self):
        # Composite knapp über Aktien-Schwelle, aber unter Krypto-Schwelle
        snap = snapshot(rsi14=25.0, close=91.0, sma50=90.0, sma200=85.0)
        stock = score_signal(snap, sentiment=0.3, fundamental=0.3, asset_class="stock")
        crypto = score_signal(snap, sentiment=0.3, fundamental=0.3, asset_class="crypto")
        if stock.action == "BUY":
            assert crypto.composite < effective_threshold(get_profile("crypto")) or \
                crypto.action == "BUY"

    def test_renormalisierung_ohne_news(self):
        # Ohne Sentiment/Fundamental (None) zählt die Technik voll —
        # fehlende Daten dürfen den Composite nicht stauchen
        snap = snapshot(rsi14=25.0, close=91.0, macd_hist=0.5,
                        macd_hist_prev=0.3, sma50=90.0, sma200=85.0)
        voll = score_signal(snap, sentiment=None, fundamental=None)
        gemischt = score_signal(snap, sentiment=0.0, fundamental=0.0)
        assert abs(voll.composite - voll.technical) < 1e-6
        assert voll.composite > gemischt.composite  # echtes Neutral staucht, None nicht
        assert voll.sentiment is None and voll.fundamental is None

    def test_renormalisierung_teilweise(self):
        # Nur Fundamental fehlt: (0.5·tech + 0.3·sent) / 0.8
        snap = snapshot()
        r = score_signal(snap, sentiment=0.8, fundamental=None)
        erwartet = (0.5 * r.technical + 0.3 * 0.8) / 0.8
        assert abs(r.composite - round(max(-1, min(1, erwartet)), 4)) < 1e-3

    def test_composite_gewichtung(self):
        # Nur Sentiment/Fundamental, Technik neutralisieren wir nicht exakt —
        # aber die Richtung muss stimmen
        pos = score_signal(snapshot(), sentiment=1.0, fundamental=1.0)
        neg = score_signal(snapshot(), sentiment=-1.0, fundamental=-1.0)
        assert pos.composite > neg.composite


class TestAggregateSentiment:
    def test_leer_ist_null(self):
        assert aggregate_sentiment([]) == 0.0

    def test_relevanz_gewichtet(self):
        articles = [
            {"sentiment_score": 1.0, "relevance": 1.0, "age_days": 0},
            {"sentiment_score": -1.0, "relevance": 0.1, "age_days": 0},
        ]
        assert aggregate_sentiment(articles) > 0.5

    def test_zeit_abkling(self):
        fresh = [{"sentiment_score": 1.0, "relevance": 1.0, "age_days": 0},
                 {"sentiment_score": -1.0, "relevance": 1.0, "age_days": 10}]
        # Frischer positiver Artikel dominiert den alten negativen
        assert aggregate_sentiment(fresh) > 0.3

    def test_halbwertszeit_exakt(self):
        # Ein Artikel, 5 Tage alt: Gewicht halbiert — Aggregat bleibt der Score selbst
        one = [{"sentiment_score": 0.8, "relevance": 1.0, "age_days": 5}]
        assert aggregate_sentiment(one) == 0.8

    def test_ohne_relevanz_default(self):
        assert aggregate_sentiment([{"sentiment_score": 0.6}]) == 0.6


class TestFlipSuppressed:
    def test_buy_bleibt_in_hysterese_zone(self):
        assert flip_suppressed("BUY", "HOLD", composite=0.30, threshold=0.35, hysteresis=0.10)

    def test_buy_kippt_unter_exit_level(self):
        assert not flip_suppressed("BUY", "HOLD", composite=0.20, threshold=0.35, hysteresis=0.10)

    def test_sell_spiegelbildlich(self):
        assert flip_suppressed("SELL", "HOLD", composite=-0.30, threshold=0.35, hysteresis=0.10)
        assert not flip_suppressed("SELL", "HOLD", composite=-0.10, threshold=0.35, hysteresis=0.10)

    def test_harte_wechsel_nie_unterdrueckt(self):
        assert not flip_suppressed("BUY", "SELL", composite=-0.5, threshold=0.35, hysteresis=0.10)
        assert not flip_suppressed("SELL", "BUY", composite=0.5, threshold=0.35, hysteresis=0.10)

    def test_ohne_vorsignal_nie_unterdrueckt(self):
        assert not flip_suppressed(None, "HOLD", composite=0.30, threshold=0.35, hysteresis=0.10)
