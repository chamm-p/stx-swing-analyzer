"""Technische Indikatoren (pandas, ohne TA-Lib-Abhängigkeit).

Berechnet RSI (Wilder), MACD, Bollinger-Bänder und gleitende
Durchschnitte. Liefert sowohl Serien (fürs Charting) als auch einen
Snapshot des letzten Werts (für Scoring und LLM-Prompt).
"""

import math

import pandas as pd


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder-Glättung = EMA mit alpha = 1/period
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    # avg_loss == 0 (nur Gewinne) → rs = inf → RSI = 100; 0/0 (flat) → NaN → 50
    rs = avg_gain / avg_loss
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (Wilder) — Basis für volatilitätsskalierte Ziele."""
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period).mean()


def bollinger(close: pd.Series, period: int = 20, num_std: float = 2.0):
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    return mid, mid + num_std * std, mid - num_std * std


def compute_indicators(df: pd.DataFrame) -> dict:
    """df: Spalten open/high/low/close/volume, Index ts (aufsteigend).

    Rückgabe: {"series": {...}, "snapshot": {...}} — snapshot enthält die
    aktuellsten Werte, series die vollen Verläufe (für Charts/API).
    """
    if df.empty or len(df) < 30:
        return {"series": {}, "snapshot": {}}

    close = df["close"]
    out_series: dict[str, pd.Series] = {}

    out_series["rsi14"] = rsi(close)
    macd_line, signal_line, hist = macd(close)
    out_series["macd"] = macd_line
    out_series["macd_signal"] = signal_line
    out_series["macd_hist"] = hist
    bb_mid, bb_up, bb_low = bollinger(close)
    out_series["bb_mid"] = bb_mid
    out_series["bb_upper"] = bb_up
    out_series["bb_lower"] = bb_low
    out_series["sma20"] = close.rolling(20).mean()
    out_series["sma50"] = close.rolling(50).mean()
    out_series["sma200"] = close.rolling(200).mean()
    out_series["ema20"] = close.ewm(span=20, adjust=False).mean()

    def last(s: pd.Series) -> float | None:
        v = s.iloc[-1]
        return None if pd.isna(v) else round(float(v), 4)

    prev_hist = hist.iloc[-2] if len(hist) >= 2 else math.nan

    atr14 = atr(df)

    snapshot = {
        "close": last(close),
        "atr14": last(atr14),
        # Swing-Marken für Kursziel/Stop (60 Handelstage ≈ 3 Monate)
        "high_60d": round(float(df["high"].tail(60).max()), 4),
        "low_60d": round(float(df["low"].tail(60).min()), 4),
        "rsi14": last(out_series["rsi14"]),
        "macd": last(macd_line),
        "macd_signal": last(signal_line),
        "macd_hist": last(hist),
        "macd_hist_prev": None if pd.isna(prev_hist) else round(float(prev_hist), 4),
        "bb_upper": last(bb_up),
        "bb_lower": last(bb_low),
        "bb_mid": last(bb_mid),
        "sma20": last(out_series["sma20"]),
        "sma50": last(out_series["sma50"]),
        "sma200": last(out_series["sma200"]),
        "pct_change_5d": last(close.pct_change(5) * 100),
        "pct_change_20d": last(close.pct_change(20) * 100),
        "volume_ratio_20d": (
            last(df["volume"] / df["volume"].rolling(20).mean())
            if "volume" in df and df["volume"].notna().any() else None
        ),
    }
    return {"series": out_series, "snapshot": snapshot}
