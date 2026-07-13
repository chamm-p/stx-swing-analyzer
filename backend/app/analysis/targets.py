"""Kursziel- und Stop-Berechnung für BUY/SELL-Signale.

Deterministische, volatilitätsskalierte Zielzone (keine Prognose):

- Ziel: Kurs ± 2·ATR(14)·√(Horizont/14) — die erwartbare Bewegung
  skaliert mit der Schwankungsbreite des Titels und der Haltedauer.
- Deckelung an der jüngsten Swing-Marke (60d-Hoch/-Tief): ein Ziel
  jenseits des letzten Widerstands/Supports wäre unseriös. Notiert der
  Kurs bereits an der Marke (Breakout), greift die ATR-Projektion.
- Stop: Kurs ∓ 1.5·ATR → Chance-Risiko-Verhältnis (CRV) = Ziel/Stop-Distanz.

Das Signal-Review misst später, wie oft die Ziele im Horizont erreicht
wurden — Basis für datenbasiertes Tuning der Faktoren.
"""

import math

TARGET_ATR_FACTOR = 2.0
STOP_ATR_FACTOR = 1.5
BASE_HORIZON_DAYS = 14


def compute_price_targets(snapshot: dict, action: str, horizon_days: int,
                          target_atr_factor: float = TARGET_ATR_FACTOR,
                          stop_atr_factor: float = STOP_ATR_FACTOR) -> dict | None:
    """Zielzone aus dem Indikator-Snapshot. None bei HOLD oder fehlenden Daten."""
    close = snapshot.get("close")
    atr = snapshot.get("atr14")
    if action not in ("BUY", "SELL") or not close or not atr or atr <= 0:
        return None

    move = target_atr_factor * atr * math.sqrt(max(horizon_days, 1) / BASE_HORIZON_DAYS)

    if action == "BUY":
        target = close + move
        swing = snapshot.get("high_60d")
        # Nur deckeln, wenn die Marke echten Abstand hat (kein Breakout)
        if swing and close + 0.5 * atr < swing < target:
            target = swing
        stop = close - stop_atr_factor * atr
    else:  # SELL (Abwärtsziel)
        target = close - move
        swing = snapshot.get("low_60d")
        if swing and target < swing < close - 0.5 * atr:
            target = swing
        stop = close + stop_atr_factor * atr

    risk = abs(close - stop)
    reward = abs(target - close)
    if risk <= 0 or reward <= 0:
        return None
    return {
        "target_price": round(target, 4),
        "stop_price": round(stop, 4),
        "risk_reward": round(reward / risk, 2),
    }
