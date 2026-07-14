"""Walk-Forward-Validierung: kalibrieren auf Fenster A, bewerten auf B.

Rollierende Fenster (train_days Kalibrierung → test_days Out-of-Sample),
Schritt = test_days. In jedem Fenster wird das Parameter-Grid auf dem
Trainingsteil bewertet (Auswahl nach Sharpe, Guard: Mindest-Trades) und
der Gewinner ungesehen auf dem Testteil gefahren. Nur die verketteten
Test-Ergebnisse zählen — das ist die ehrliche Schätzung.
"""

from __future__ import annotations

import logging
import math
from itertools import product

import pandas as pd

from app.backtest.engine import run_backtest
from app.backtest.metrics import compute_metrics
from app.backtest.params import StrategyConfig

logger = logging.getLogger(__name__)

MAX_COMBOS = 200


def build_grid(grid: dict[str, list]) -> list[dict]:
    """Kartesisches Produkt der Parameterlisten (gedeckelt)."""
    grid = {k: v for k, v in grid.items() if isinstance(v, list) and v}
    if not grid:
        return [{}]
    keys = list(grid.keys())
    combos = [dict(zip(keys, values)) for values in product(*grid.values())]
    if len(combos) > MAX_COMBOS:
        raise ValueError(f"Grid zu groß ({len(combos)} Kombinationen, max {MAX_COMBOS})")
    return combos


def _selection_score(metrics: dict) -> float:
    sharpe = metrics.get("sharpe")
    if sharpe is not None:
        return float(sharpe)
    return float(metrics.get("total_return_pct") or -999)


def walk_forward(
    data: dict[str, pd.DataFrame],
    base_params: dict,
    grid: dict[str, list],
    currencies: dict[str, str] | None = None,
    fees: dict | None = None,
    train_days: int = 365,
    test_days: int = 90,
    min_trades: int = 20,
    min_train_score: float | None = 0.0,
) -> dict:
    """min_train_score: Flat-Guard — liegt selbst der beste Grid-Kandidat
    im Training unter dieser Score-Qualität, wird im Testfenster NICHT
    gehandelt (Cash). „Der beste von lauter schlechten" ist kein Grund
    zu handeln. None deaktiviert den Guard."""
    combos = build_grid(grid)
    all_dates = sorted(set().union(*[set(df.index) for df in data.values()]))
    if len(all_dates) < 250:
        raise ValueError("Zu wenig Historie für Walk-Forward")

    warmup = int(base_params.get("warmup_days") or 210)
    span_start = all_dates[min(warmup, len(all_dates) - 1)]
    last_date = all_dates[-1]

    def cfg(overrides: dict) -> StrategyConfig:
        merged = {**base_params, **overrides}
        merged.pop("fees", None)
        return StrategyConfig(**merged, fees=fees)

    windows: list[dict] = []
    combined: list[dict] = []
    running_capital = float(base_params.get("start_capital") or 10_000)
    start_capital = running_capital
    param_wins: dict[str, int] = {}

    cursor = span_start
    while cursor + pd.Timedelta(days=train_days + test_days) <= last_date:
        train_start = cursor
        train_end = cursor + pd.Timedelta(days=train_days)
        test_end = train_end + pd.Timedelta(days=test_days)

        # 1) Kalibrierung: bestes Grid-Element im Trainingsfenster
        best: tuple[float, dict, dict] | None = None
        for combo in combos:
            result = run_backtest(data, cfg(combo), currencies,
                                  trade_start=train_start, trade_end=train_end)
            metrics = compute_metrics(result)
            if (metrics.get("num_trades") or 0) < min_trades:
                continue
            score = _selection_score(metrics)
            if best is None or score > best[0]:
                best = (score, combo, metrics)

        window: dict = {
            "train": [train_start.strftime("%Y-%m-%d"), train_end.strftime("%Y-%m-%d")],
            "test": [train_end.strftime("%Y-%m-%d"), test_end.strftime("%Y-%m-%d")],
        }
        if best is None:
            window["skipped"] = f"kein Grid-Element mit >= {min_trades} Trades"
            windows.append(window)
            cursor += pd.Timedelta(days=test_days)
            continue

        train_score, chosen, train_metrics = best

        # Flat-Guard: schlechte Gewinner werden nicht gehandelt
        if min_train_score is not None and train_score < min_train_score:
            window["flat"] = (f"bester Train-Score {round(train_score, 2)} "
                              f"< {min_train_score} — Fenster in Cash")
            window["train_score"] = round(train_score, 3)
            windows.append(window)
            cursor += pd.Timedelta(days=test_days)
            continue
        # 2) Out-of-Sample: Gewinner ungesehen auf dem Testfenster
        test_result = run_backtest(data, cfg(chosen), currencies,
                                   trade_start=train_end, trade_end=test_end)
        test_metrics = compute_metrics(test_result)

        window.update({
            "chosen_params": chosen,
            "train_score": round(train_score, 3),
            "train_return_pct": train_metrics.get("total_return_pct"),
            "test_return_pct": test_metrics.get("total_return_pct"),
            "test_trades": test_metrics.get("num_trades"),
            "test_win_rate": test_metrics.get("win_rate"),
        })
        windows.append(window)
        param_wins[str(chosen)] = param_wins.get(str(chosen), 0) + 1

        # 3) Test-Equity an die laufende Kapitalkurve ketten
        equity = test_result.equity
        if len(equity):
            factor = running_capital / float(base_params.get("start_capital") or 10_000)
            for ts, value in equity.items():
                combined.append({"time": ts.strftime("%Y-%m-%d"),
                                 "value": round(float(value) * factor, 2)})
            running_capital = combined[-1]["value"]

        cursor += pd.Timedelta(days=test_days)

    # Aggregierte Out-of-Sample-Kennzahlen aus der verketteten Kurve
    oos: dict = {"windows_total": len(windows),
                 "windows_tested": sum(1 for w in windows if "chosen_params" in w),
                 "windows_flat": sum(1 for w in windows if "flat" in w)}
    if combined:
        values = pd.Series([p["value"] for p in combined])
        oos["total_return_pct"] = round((float(values.iloc[-1]) / start_capital - 1) * 100, 2)
        running_max = values.cummax()
        oos["max_drawdown_pct"] = round(float(((values - running_max) / running_max).min()) * 100, 2)
        returns = values.pct_change().dropna()
        if len(returns) > 10 and float(returns.std()) > 0:
            oos["sharpe"] = round(float(returns.mean()) / float(returns.std()) * math.sqrt(252), 2)
        oos["num_trades"] = sum(w.get("test_trades") or 0 for w in windows)
        test_returns = [w["test_return_pct"] for w in windows if w.get("test_return_pct") is not None]
        if test_returns:
            oos["positive_windows"] = sum(1 for r in test_returns if r > 0)
        # Stabilität: gewinnt immer derselbe Parametersatz?
        if param_wins:
            top = max(param_wins.values())
            oos["param_stability"] = round(top / max(oos["windows_tested"], 1), 2)

    return {"windows": windows, "oos": oos, "equity": combined,
            "param_wins": param_wins}
