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
    sentiment: float
    fundamental: float
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
        if prev is not None:
            momentum = 0.3 if hist > prev else -0.3
        components["macd"] = max(-1.0, min(1.0, base + momentum))

    # Bollinger: Schluss unter unterem Band → Mean-Reversion-Chance, über oberem → Überhitzung.
    bb_low, bb_up, c = ind.get("bb_lower"), ind.get("bb_upper"), ind.get("close")
    if None not in (bb_low, bb_up, c) and bb_up > bb_low:
        pos = (c - bb_low) / (bb_up - bb_low)  # 0 = unteres, 1 = oberes Band
        components["bollinger"] = max(-1.0, min(1.0, (0.5 - pos) * 2))

    # Trend: Kurs relativ zu SMA50/SMA200 (Trendfolge-Komponente).
    sma50, sma200 = ind.get("sma50"), ind.get("sma200")
    if c is not None and sma50 is not None:
        trend = 0.5 if c > sma50 else -0.5
        if sma200 is not None:
            trend += 0.5 if sma50 > sma200 else -0.5  # Golden/Death-Cross-Kontext
        components["trend"] = max(-1.0, min(1.0, trend))

    if not components:
        return 0.0, components
    # Gewichtung: Mean-Reversion (RSI/BB) und Trend/Momentum (MACD/MAs) gleichrangig.
    weights = {"rsi": 0.25, "macd": 0.25, "bollinger": 0.2, "trend": 0.3}
    total_w = sum(weights[k] for k in components)
    score = sum(components[k] * weights[k] for k in components) / total_w
    return round(score, 4), {k: round(v, 4) for k, v in components.items()}


def aggregate_sentiment(articles: list[dict]) -> float:
    """Relevanz-gewichteter Sentiment-Mittelwert der jüngsten Artikel."""
    weighted, total = 0.0, 0.0
    for a in articles:
        s = a.get("sentiment_score")
        if s is None:
            continue
        w = a.get("relevance") or 0.5
        weighted += s * w
        total += w
    return round(weighted / total, 4) if total > 0 else 0.0


def effective_threshold(profile: ScoringProfile) -> float:
    s = get_settings()
    return s.score_threshold_crypto if profile.use_crypto_threshold else s.score_threshold


def score_signal(indicator_snapshot: dict, sentiment: float, fundamental: float,
                 asset_class: str = "stock") -> ScoringResult:
    s = get_settings()
    profile = get_profile(asset_class)
    tech, components = technical_score(indicator_snapshot, profile)
    threshold = effective_threshold(profile)

    composite = (
        s.score_weight_technical * tech
        + s.score_weight_sentiment * sentiment
        + s.score_weight_fundamental * fundamental
    )
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
    directions = [v for v in (tech, sentiment, fundamental) if abs(v) > 0.1]
    if len(directions) >= 2 and (all(d > 0 for d in directions) or all(d < 0 for d in directions)):
        confidence = min(1.0, confidence + 0.15)

    return ScoringResult(
        action=action,
        confidence=round(confidence, 3),
        composite=round(composite, 4),
        technical=tech,
        sentiment=sentiment,
        fundamental=round(fundamental, 4),
        components=components,
        profile=profile.name,
    )
