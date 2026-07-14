"""Tests für die Backtest-Engine: Trade-Mechanik deterministisch über
injizierte Scores, plus Metriken und ein Integrationslauf mit echtem
Scoring auf synthetischen Daten."""

import numpy as np
import pandas as pd

from app.backtest.engine import BacktestResult, Trade, run_backtest
from app.backtest.metrics import buy_and_hold_return, compute_metrics
from app.backtest.params import StrategyConfig


def make_df(closes, highs=None, lows=None, opens=None) -> pd.DataFrame:
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "open": np.asarray(opens, dtype=float) if opens is not None else c,
        "high": np.asarray(highs, dtype=float) if highs is not None else c * 1.01,
        "low": np.asarray(lows, dtype=float) if lows is not None else c * 0.99,
        "close": c,
        "volume": np.full(len(c), 1_000_000),
    }, index=pd.date_range("2024-01-01", periods=len(c), freq="B"))


def flat(n=300, price=100.0):
    return [price] * n


CFG = StrategyConfig(start_capital=10_000, position_size=1_000, max_positions=10,
                     slippage_bps=0.0, warmup_days=210, horizon_days=14,
                     cooldown_days=3, threshold=0.35)

SIGNAL_DAY = 250


def run_with_signal(df, day_index=SIGNAL_DAY, config=CFG):
    """Backtest mit Score=+1 an genau einem Tag (Marker: eindeutiger
    Schlusskurs am Signal-Tag)."""
    marker = df["close"].iloc[day_index]

    def score_fn(symbol, snapshot):
        return 1.0 if snapshot["close"] == marker else 0.0

    return run_backtest({"SYM": df}, config, score_fn=score_fn)


class TestTradeMechanik:
    def test_einstieg_zum_open_des_folgetags(self):
        closes = flat()
        closes[SIGNAL_DAY] = 100.5  # eindeutiger Signal-Marker
        df = make_df(closes)
        result = run_with_signal(df)
        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.entry_date == df.index[SIGNAL_DAY + 1]  # kein Look-Ahead
        assert trade.entry_price == df["open"].iloc[SIGNAL_DAY + 1]
        assert trade.target_price and trade.stop_price

    def test_take_profit_intraday(self):
        closes = flat()
        closes[SIGNAL_DAY] = 100.5
        highs = [c * 1.01 for c in closes]
        # ATR ~2 → Ziel ~104.5; drei Tage nach Einstieg schießt das Hoch drüber
        highs[SIGNAL_DAY + 4] = 120.0
        df = make_df(closes, highs=highs)
        result = run_with_signal(df)
        trade = result.trades[0]
        assert trade.reason == "target"
        assert trade.exit_date == df.index[SIGNAL_DAY + 4]
        assert trade.pnl > 0

    def test_stop_loss_intraday_und_stop_vor_ziel(self):
        closes = flat()
        closes[SIGNAL_DAY] = 100.5
        highs = [c * 1.01 for c in closes]
        lows = [c * 0.99 for c in closes]
        # Am selben Tag beides berührt → Stop gewinnt (konservativ)
        highs[SIGNAL_DAY + 2] = 130.0
        lows[SIGNAL_DAY + 2] = 80.0
        df = make_df(closes, highs=highs, lows=lows)
        result = run_with_signal(df)
        trade = result.trades[0]
        assert trade.reason == "stop"
        assert trade.pnl < 0

    def test_horizont_exit(self):
        closes = flat()
        closes[SIGNAL_DAY] = 100.5
        df = make_df(closes)
        result = run_with_signal(df)
        trade = result.trades[0]
        assert trade.reason == "horizon"
        assert (trade.exit_date - trade.entry_date).days >= CFG.horizon_days

    def test_max_positions_begrenzt(self):
        closes = flat()
        closes[SIGNAL_DAY] = 100.5
        df = make_df(closes)
        cfg = StrategyConfig(**{**CFG.to_dict(), "max_positions": 1})

        def score_fn(symbol, snapshot):
            return 1.0 if snapshot["close"] == 100.5 else 0.0

        result = run_backtest({"A": df, "B": df.copy(), "C": df.copy()}, cfg, score_fn=score_fn)
        # Nur 1 Position gleichzeitig; nach deren Exit darf neu gekauft werden
        assert max_gleichzeitig(result) == 1

    def test_gebuehren_gebucht(self):
        closes = flat()
        closes[SIGNAL_DAY] = 100.5
        df = make_df(closes)
        fees = {"default": [{"up_to": 1000, "fee": 5.0}, {"up_to": None, "fee": 10.0}]}
        cfg = StrategyConfig(**{**CFG.to_dict(), "fees": fees})

        def score_fn(symbol, snapshot):
            return 1.0 if snapshot["close"] == 100.5 else 0.0

        result = run_backtest({"SYM": df}, cfg, score_fn=score_fn)
        trade = result.trades[0]
        assert trade.fee_buy == 5.0   # Volumen 1000 → Stufe 1
        assert trade.fee_sell > 0
        # P/L bei flachem Kurs ≈ -Gebühren
        assert trade.pnl < 0


def max_gleichzeitig(result: BacktestResult) -> int:
    events = []
    for t in result.trades:
        events.append((t.entry_date, 1))
        events.append((t.exit_date or t.entry_date, -1))
    events.sort(key=lambda e: (e[0], -e[1]))
    peak = cur = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    return peak


class TestMetriken:
    def _result(self, equity_values, trades=None):
        idx = pd.date_range("2024-01-01", periods=len(equity_values), freq="B")
        return BacktestResult(
            config=CFG, equity=pd.Series(equity_values, index=idx, dtype=float),
            trades=trades or [], cash_final=0.0,
        )

    def test_rendite_und_drawdown(self):
        m = compute_metrics(self._result([10_000, 11_000, 9_900, 12_000]))
        assert m["total_return_pct"] == 20.0
        assert m["max_drawdown_pct"] == -10.0

    def test_trade_statistik(self):
        idx = pd.date_range("2024-01-01", periods=4, freq="B")
        trades = [
            Trade("A", idx[0], 100, 1, exit_date=idx[1], exit_price=110, reason="target"),
            Trade("B", idx[0], 100, 1, exit_date=idx[2], exit_price=95, reason="stop"),
            Trade("C", idx[1], 100, 1, exit_date=idx[3], exit_price=104, reason="horizon"),
        ]
        m = compute_metrics(self._result([10_000] * 4, trades))
        assert m["num_trades"] == 3
        assert m["win_rate"] == round(2 / 3, 3)
        assert m["profit_factor"] == round((10 + 4) / 5, 2)
        assert m["exit_reasons"]["target"] == 1

    def test_buy_and_hold(self):
        df = make_df([100, 105, 110, 120])
        assert buy_and_hold_return(df, df.index[0], df.index[-1]) == 20.0


class TestHandelsfenster:
    def test_kein_handel_vor_fensterstart(self):
        closes = flat()
        closes[SIGNAL_DAY] = 100.5
        df = make_df(closes)
        marker = df["close"].iloc[SIGNAL_DAY]

        def score_fn(symbol, snapshot):
            return 1.0 if snapshot["close"] == marker else 0.0

        # Fenster beginnt erst NACH dem Signaltag → kein Trade
        result = run_backtest({"SYM": df}, CFG, score_fn=score_fn,
                              trade_start=df.index[SIGNAL_DAY + 10],
                              trade_end=df.index[-1])
        assert len(result.trades) == 0

    def test_fensterende_stellt_glatt(self):
        closes = flat()
        closes[SIGNAL_DAY] = 100.5
        df = make_df(closes)
        marker = df["close"].iloc[SIGNAL_DAY]

        def score_fn(symbol, snapshot):
            return 1.0 if snapshot["close"] == marker else 0.0

        end = df.index[SIGNAL_DAY + 4]  # vor Horizont-Ablauf
        result = run_backtest({"SYM": df}, CFG, score_fn=score_fn,
                              trade_start=df.index[0], trade_end=end)
        trade = result.trades[0]
        assert trade.reason == "window_end"
        assert trade.exit_date == end
        # Equity endet am Fensterende
        assert result.equity.index[-1] == end


class TestWalkForward:
    def test_grid_kartesisch_und_deckel(self):
        from app.backtest.walkforward import build_grid
        combos = build_grid({"threshold": [0.3, 0.4], "position_size": [1000, 2000]})
        assert len(combos) == 4
        assert {"threshold": 0.3, "position_size": 2000} in combos
        assert build_grid({}) == [{}]

    def test_walkforward_struktur(self):
        from app.backtest.walkforward import walk_forward
        # 700 Handelstage Zufallslauf — es geht um Struktur, nicht Alpha
        rng = np.random.default_rng(7)
        closes = 100 * np.cumprod(1 + rng.normal(0.0004, 0.015, 700))
        data = {"SYM": make_df(list(closes))}
        base = {**CFG.to_dict()}
        base.pop("fees", None)
        wf = walk_forward(data, base, {"threshold": [0.3, 0.4]},
                          train_days=200, test_days=80, min_trades=0)
        assert wf["oos"]["windows_total"] >= 2
        for w in wf["windows"]:
            assert "test" in w and "train" in w
            # Testfenster beginnt, wo Training endet (kein Überlappen)
            assert w["train"][1] == w["test"][0]
        if wf["equity"]:
            times = [p["time"] for p in wf["equity"]]
            assert times == sorted(times)

    def test_flat_guard_verhindert_schlechte_fenster(self):
        from app.backtest.walkforward import walk_forward
        rng = np.random.default_rng(7)
        closes = 100 * np.cumprod(1 + rng.normal(0.0004, 0.015, 700))
        data = {"SYM": make_df(list(closes))}
        base = {**CFG.to_dict()}
        base.pop("fees", None)
        # Unerreichbar hoher Guard → ALLE Fenster flat, keine Trades
        wf = walk_forward(data, base, {"threshold": [0.3]},
                          train_days=200, test_days=80, min_trades=0,
                          min_train_score=99.0)
        assert wf["oos"]["windows_flat"] == wf["oos"]["windows_total"]
        assert wf["equity"] == []
        for w in wf["windows"]:
            assert "flat" in w


class TestIntegrationEchtesScoring:
    def test_lauf_ohne_injektion_stabil(self):
        # Trendwechsel-Muster: Anstieg, Einbruch, Erholung — es geht nur um
        # Stabilität und Konsistenz, nicht um konkrete Signale
        n = 400
        base = np.concatenate([
            np.linspace(100, 150, 150),
            np.linspace(150, 110, 100),
            np.linspace(110, 160, 150),
        ])
        rng = np.random.default_rng(42)
        closes = base * (1 + rng.normal(0, 0.01, n))
        df = make_df(list(closes))
        result = run_backtest({"SYM": df}, CFG)
        assert len(result.equity) > 0
        assert result.equity.notna().all()
        # Buchhaltung konsistent: Equity-Endwert = Cash + offene Positionen
        open_value = sum(
            t.quantity * df["close"].iloc[-1] for t in result.trades if t.exit_price is None
        )
        assert abs(float(result.equity.iloc[-1]) - (result.cash_final + open_value)) < 1.0
        metrics = compute_metrics(result)
        assert "total_return_pct" in metrics
