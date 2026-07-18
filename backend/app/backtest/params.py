"""Strategie-Parameter für Backtests — alles Kalibrierbare an einem Ort.

Die Defaults entsprechen exakt den Live-Parametern (stock-Profil), damit
„Backtest mit aktuellen Parametern" die produktive Strategie abbildet.
"""

from dataclasses import asdict, dataclass, field

from app.analysis.scoring import ScoringProfile


@dataclass(frozen=True)
class StrategyConfig:
    # Technisches Scoring (siehe analysis/scoring.py)
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    rsi_scale: float = 15.0
    macd_scale: float = 100.0
    w_rsi: float = 0.25
    w_macd: float = 0.25
    w_bollinger: float = 0.2
    w_trend: float = 0.3

    # Signal-Schwelle (BUY >= threshold; Exit-Signal <= -threshold)
    threshold: float = 0.35

    # Zielzone/Stop (analysis/targets.py)
    target_atr_factor: float = 2.0
    stop_atr_factor: float = 1.5
    horizon_days: int = 14  # Kalendertage

    # Strategie-Familie: "meanrev" (Schwäche kaufen, ATR-Fixziel),
    # "momentum" (Stärke kaufen, Trailing-Stop) oder "dtt" (Dual-Timeframe
    # Trendfolge: Golden-Cross-Einstieg, Stop am Swing-Low, R-Fixziel)
    strategy_kind: str = "meanrev"
    trailing_stop_atr: float = 0.0  # >0: Trailing-Stop in ATR, Fixziel aus
    regime_sma: int = 0             # >0: Einstieg nur wenn Close > SMA(n)
    # DTT: Ziel als Vielfaches des Risikos (Einstieg−Stop); Break-even bei
    target_r: float = 0.0           # >0: Ziel = Einstieg + target_r × R
    breakeven_r: float = 0.0        # >0: Stop → Einstieg, sobald +breakeven_r × R

    # Portfolio-Regeln (wie Auto-Trader)
    start_capital: float = 10_000.0
    position_size: float = 1_000.0
    max_positions: int = 10
    cooldown_days: int = 3

    # Ausführung
    slippage_bps: float = 5.0  # 0.05% je Fill
    fees: dict | None = None   # Plattform-Staffel ({"default": [...]}) oder None
    warmup_days: int = 210     # Handelstage bis SMA200 & Co. stabil sind

    def profile(self) -> ScoringProfile:
        return ScoringProfile(
            name="backtest",
            rsi_oversold=self.rsi_oversold,
            rsi_overbought=self.rsi_overbought,
            rsi_scale=self.rsi_scale,
            macd_scale=self.macd_scale,
            w_rsi=self.w_rsi, w_macd=self.w_macd,
            w_bollinger=self.w_bollinger, w_trend=self.w_trend,
        )

    def to_dict(self) -> dict:
        return asdict(self)
