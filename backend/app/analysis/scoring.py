"""Regelbasierter Signal-Scoring-Layer.

Deterministisch und reproduzierbar: identische Eingaben (Indikator-
Snapshot, Sentiment-Aggregat, Fundamental-Score) ergeben identische
Signale. Alle Teil-Scores liegen in [-1, 1]; positiv = bullish.
"""

from dataclasses import dataclass

from app.config import get_settings


@dataclass(frozen=True)
class ScoringProfile:
    """Asset-Klassen-spezifische Scoring-Parameter.

    Krypto handelt 24/7 und deutlich volatiler als Aktien: RSI erreicht
    dort schneller Extremwerte (→ Schwellen 25/75 statt 30/70, sonst
    feuert Mean-Reversion zu früh), das MACD-Histogramm ist relativ zum
    Kurs größer (→ schwächere Normierung, sonst sättigt die Komponente
    bei ±1 und trägt keine Information mehr) und das Grundrauschen ist
    höher (→ höhere Composite-Schwelle für BUY/SELL).
    """

    name: str
    rsi_oversold: float
    rsi_overbought: float
    rsi_scale: float    # Divisor für die Skalierung jenseits der Schwellen
    macd_scale: float   # Multiplikator für hist/close-Normierung
    use_crypto_threshold: bool = False
    # Gewichte der Technik-Komponenten (kalibrierbar via Backtesting)
    w_rsi: float = 0.25
    w_macd: float = 0.25
    w_bollinger: float = 0.2
    w_trend: float = 0.3


PROFILES = {
    "stock": ScoringProfile("stock", rsi_oversold=30, rsi_overbought=70,
                            rsi_scale=15, macd_scale=100),
    "crypto": ScoringProfile("crypto", rsi_oversold=25, rsi_overbought=75,
                             rsi_scale=12, macd_scale=60, use_crypto_threshold=True),
}


def get_profile(asset_class: str | None) -> ScoringProfile:
    return PROFILES.get(asset_class or "stock", PROFILES["stock"])


@dataclass
class ScoringResult:
    action: str  # BUY | SELL | HOLD
    confidence: float  # 0..1
    composite: float  # -1..1
    technical: float
    sentiment: float | None  # None = keine News-Basis
    fundamental: float | None  # None = nicht verfügbar
    components: dict  # Einzelregeln für die Begründung
    profile: str = "stock"


def technical_score(ind: dict, profile: ScoringProfile = PROFILES["stock"]) -> tuple[float, dict]:
    """Kombiniert RSI, MACD, Bollinger und MA-Lage zu einem Score in [-1, 1]."""
    components: dict[str, float] = {}

    # RSI: unter Schwelle überverkauft (bullish für Mean-Reversion-Swing),
    # über Schwelle überkauft. Schwellen und Skalierung aus dem Profil.
    r = ind.get("rsi14")
    if r is not None:
        if r <= profile.rsi_oversold:
            components["rsi"] = min(1.0, (profile.rsi_oversold - r) / profile.rsi_scale + 0.5)
        elif r >= profile.rsi_overbought:
            components["rsi"] = -min(1.0, (r - profile.rsi_overbought) / profile.rsi_scale + 0.5)
        else:
            components["rsi"] = (50 - r) / 40  # leicht kontraindikativ um die Mitte

    # MACD: Vorzeichen des Histogramms + Momentum (steigend/fallend).
    hist, prev = ind.get("macd_hist"), ind.get("macd_hist_prev")
    close = ind.get("close") or 1.0
    if hist is not None:
        base = max(-1.0, min(1.0, (hist / close) * profile.macd_scale))  # normiert auf Kurs
        momentum = 0.0
        if prev is not None and hist != prev:
            momentum = 0.3 if hist > prev else -0.3
        components["macd"] = max(-1.0, min(1.0, base + momentum))

    # Bollinger: Schluss unter unterem Band → Mean-Reversion-Chance, über oberem → Überhitzung.
    bb_low, bb_up, c = ind.get("bb_lower"), ind.get("bb_upper"), ind.get("close")
    if None not in (bb_low, bb_up, c) and bb_up > bb_low:
        pos = (c - bb_low) / (bb_up - bb_low)  # 0 = unteres, 1 = oberes Band
        components["bollinger"] = max(-1.0, min(1.0, (0.5 - pos) * 2))

    # Trend: Kurs relativ zu SMA50/SMA200 (Trendfolge-Komponente).
    # Exakter Gleichstand ist neutral — nicht bearish.
    def sign(a: float, b: float) -> float:
        return 0.5 if a > b else (-0.5 if a < b else 0.0)

    sma50, sma200 = ind.get("sma50"), ind.get("sma200")
    if c is not None and sma50 is not None:
        trend = sign(c, sma50)
        if sma200 is not None:
            trend += sign(sma50, sma200)  # Golden/Death-Cross-Kontext
        components["trend"] = max(-1.0, min(1.0, trend))

    if not components:
        return 0.0, components
    # Gewichtung: Mean-Reversion (RSI/BB) und Trend/Momentum (MACD/MAs) gleichrangig.
    weights = {"rsi": profile.w_rsi, "macd": profile.w_macd,
               "bollinger": profile.w_bollinger, "trend": profile.w_trend}
    total_w = sum(weights[k] for k in components)
    score = sum(components[k] * weights[k] for k in components) / total_w
    return round(score, 4), {k: round(v, 4) for k, v in components.items()}


SENTIMENT_HALF_LIFE_DAYS = 5.0


def aggregate_sentiment(articles: list[dict], half_life_days: float = SENTIMENT_HALF_LIFE_DAYS) -> float:
    """Sentiment-Aggregat: LLM-Relevanz × exponentieller Zeit-Abkling.

    Ein 5 Tage alter Artikel zählt halb so viel wie einer von heute —
    Nachrichtenlage ist im Swing-Horizont schnell verderblich."""
    weighted, total = 0.0, 0.0
    for a in articles:
        s = a.get("sentiment_score")
        if s is None:
            continue
        relevance = a.get("relevance")
        relevance = 0.5 if relevance is None else max(0.0, min(1.0, relevance))
        age_days = max(0.0, float(a.get("age_days") or 0.0))
        w = relevance * 0.5 ** (age_days / half_life_days)
        weighted += s * w
        total += w
    return round(weighted / total, 4) if total > 0 else 0.0


def flip_suppressed(last_action: str | None, new_action: str, composite: float,
                    threshold: float, hysteresis: float) -> bool:
    """Hysterese: BUY/SELL kippt nur auf HOLD zurück, wenn der Composite
    DEUTLICH unter die Schwelle gefallen ist (Einstieg >= Schwelle,
    Ausstieg < Schwelle - Hysterese). Harte BUY<->SELL-Wechsel: nie
    unterdrückt."""
    if last_action not in ("BUY", "SELL") or new_action != "HOLD":
        return False
    exit_level = threshold - hysteresis
    if last_action == "BUY":
        return composite > exit_level
    return composite < -exit_level


def effective_threshold(profile: ScoringProfile) -> float:
    s = get_settings()
    return s.score_threshold_crypto if profile.use_crypto_threshold else s.score_threshold


def score_signal(indicator_snapshot: dict, sentiment: float | None,
                 fundamental: float | None,
                 asset_class: str = "stock") -> ScoringResult:
    """Composite-Score mit verfügbarkeitsbasierter Gewichtung.

    sentiment/fundamental = None heißt „nicht verfügbar" (z.B. keine
    News zum Wert) — dann wird das Gewicht auf die vorhandenen
    Komponenten renormalisiert. Fehlende Daten dürfen den Score nicht
    wie neutrale Daten stauchen: Ein Wert ohne Nachrichtenlage ist ein
    rein technisches Signal (wie im Screener), kein halbiertes."""
    s = get_settings()
    profile = get_profile(asset_class)
    tech, components = technical_score(indicator_snapshot, profile)
    threshold = effective_threshold(profile)

    parts: list[tuple[float, float]] = [(tech, s.score_weight_technical)]
    if sentiment is not None:
        parts.append((sentiment, s.score_weight_sentiment))
    if fundamental is not None:
        parts.append((fundamental, s.score_weight_fundamental))
    total_weight = sum(w for _, w in parts)
    composite = sum(v * w for v, w in parts) / total_weight if total_weight else 0.0
    composite = max(-1.0, min(1.0, composite))

    if composite >= threshold:
        action = "BUY"
    elif composite <= -threshold:
        action = "SELL"
    else:
        action = "HOLD"

    # Confidence: Stärke des Composite, mit Bonus wenn alle Teil-Scores
    # in dieselbe Richtung zeigen (Agreement).
    confidence = min(1.0, abs(composite) / max(threshold * 2, 0.01))
    directions = [v for v in (tech, sentiment, fundamental)
                  if v is not None and abs(v) > 0.1]
    if len(directions) >= 2 and (all(d > 0 for d in directions) or all(d < 0 for d in directions)):
        confidence = min(1.0, confidence + 0.15)

    return ScoringResult(
        action=action,
        confidence=round(confidence, 3),
        composite=round(composite, 4),
        technical=tech,
        sentiment=sentiment,
        fundamental=round(fundamental, 4) if fundamental is not None else None,
        components=components,
        profile=profile.name,
    )
