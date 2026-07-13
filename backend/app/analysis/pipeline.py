"""Analyse-Pipeline: Indikatoren → LLM → Scoring → Signal → Alert.

Wird vom Scheduler pro Watchlist-Symbol ausgeführt. Ein neues Signal
entsteht nur bei Richtungswechsel oder wenn das letzte Signal älter als
SIGNAL_REFRESH_HOURS ist — sonst würde jeder Lauf ein Duplikat erzeugen.
"""

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.dispatcher import dispatch_signal_alert
from app.analysis.llm_analysis import analyze_pending_sentiment, asset_review, recent_scored_articles
from app.analysis.scoring import (
    aggregate_sentiment, effective_threshold, get_profile, score_signal,
)
from app.analysis.targets import compute_price_targets
from app.analysis.watch_scope import alert_config, effective_symbols
from app.config import get_settings
from app.llm.client import LLMClient, LLMError
from app.models import Asset, Signal, utcnow
from app.processing.indicators import compute_indicators
from app.sources.yahoo import load_ohlcv_df

logger = logging.getLogger(__name__)


async def run_for_symbol(db: AsyncSession, symbol: str) -> Signal | None:
    settings = get_settings()
    asset = await db.get(Asset, symbol)
    if asset is None:
        logger.warning("Pipeline: Asset %s nicht gefunden", symbol)
        return None

    df = await load_ohlcv_df(db, symbol)
    if df.empty or len(df) < 30:
        logger.info("Pipeline %s: zu wenig Kursdaten (%d Kerzen) — übersprungen", symbol, len(df))
        return None
    indicators = compute_indicators(df)
    snapshot = indicators["snapshot"]

    llm = await LLMClient.create(db)

    # 1) Sentiment für neue Artikel
    await analyze_pending_sentiment(db, llm, symbol, asset.name)
    articles = await recent_scored_articles(db, symbol)
    sentiment = aggregate_sentiment(articles)

    # 2) LLM-Gesamteinschätzung (fundamental) — fail-soft auf neutral
    fundamental, review = 0.0, {}
    try:
        review = await asset_review(db, llm, asset, snapshot, articles)
        fundamental = max(-1.0, min(1.0, float(review.get("fundamental_score", 0.0))))
    except (LLMError, ValueError, TypeError) as e:
        logger.warning("Asset-Review fehlgeschlagen für %s: %s — fundamental=0", symbol, e)

    # 3) Regelbasiertes Scoring (Profil nach Asset-Klasse: stock/crypto)
    asset_class = "crypto" if asset.asset_type == "crypto" else "stock"
    result = score_signal(snapshot, sentiment, fundamental, asset_class=asset_class)

    # 4) Signal-Erzeugung (Dedupe + Hysterese gegen Flattern)
    last = await db.scalar(
        select(Signal).where(Signal.symbol == symbol).order_by(Signal.ts.desc()).limit(1)
    )
    refresh_due = last is None or (utcnow() - last.ts) > timedelta(hours=settings.signal_refresh_hours)
    direction_change = last is not None and last.action != result.action

    # Hysterese: Ein BUY/SELL kippt nur dann vorzeitig auf HOLD zurück,
    # wenn der Composite DEUTLICH unter die Schwelle gefallen ist — sonst
    # erzeugt LLM-Varianz um die Schwelle herum HOLD→BUY→HOLD-Pingpong
    # bei unverändertem Kurs. (Nach SIGNAL_REFRESH_HOURS greift der
    # normale Refresh und stellt den ehrlichen Zustand wieder her.)
    if direction_change and not refresh_due and result.action == "HOLD":
        threshold = effective_threshold(get_profile(asset_class))
        exit_level = threshold - settings.signal_hysteresis
        holds_buy = last.action == "BUY" and result.composite > exit_level
        holds_sell = last.action == "SELL" and result.composite < -exit_level
        if holds_buy or holds_sell:
            logger.info("Pipeline %s: %s bleibt bestehen (Hysterese; Composite %.2f, "
                        "Ausstieg erst < %.2f)", symbol, last.action,
                        result.composite, exit_level)
            return None

    if not (refresh_due or direction_change):
        logger.info("Pipeline %s: %s (%.2f) — kein neues Signal (Dedupe)",
                    symbol, result.action, result.composite)
        return None

    horizon_days = int(review.get("suggested_horizon_days") or 14)

    # Kursziel/Stop/CRV (ATR-Zielzone) + Analysten-Konsens (nur Aktien/ETFs)
    targets = compute_price_targets(snapshot, result.action, horizon_days) or {}
    analyst: dict = {}
    if result.action in ("BUY", "SELL") and asset.asset_type != "crypto":
        try:
            from app.sources.yahoo import fetch_analyst_targets
            analyst = await fetch_analyst_targets(symbol)
        except Exception as e:
            logger.warning("Analystenziele für %s nicht abrufbar: %s", symbol, e)

    rationale_parts = [
        f"Technisch {result.technical:+.2f} [Profil {result.profile}] (" +
        ", ".join(f"{k} {v:+.2f}" for k, v in result.components.items()) + ")",
        f"Sentiment {result.sentiment:+.2f} aus {len(articles)} News",
        f"Fundamental {result.fundamental:+.2f}",
    ]
    if targets:
        rationale_parts.append(
            f"Ziel {targets['target_price']} · Stop {targets['stop_price']} · "
            f"CRV 1:{targets['risk_reward']}"
        )
    if analyst.get("mean"):
        rationale_parts.append(f"Analysten-Konsens {analyst['mean']} ({analyst.get('count')} Schätzungen)")
    if review.get("summary"):
        rationale_parts.append(str(review["summary"]))

    signal = Signal(
        symbol=symbol,
        action=result.action,
        confidence=result.confidence,
        composite_score=result.composite,
        technical_score=result.technical,
        sentiment_score=result.sentiment,
        fundamental_score=result.fundamental,
        rationale=" — ".join(rationale_parts),
        horizon_days=horizon_days,
        indicators=snapshot,
        price_at_signal=snapshot.get("close"),
        target_price=targets.get("target_price"),
        stop_price=targets.get("stop_price"),
        risk_reward=targets.get("risk_reward"),
        analyst_target=analyst.get("mean"),
        analyst_count=analyst.get("count"),
    )
    db.add(signal)
    await db.commit()
    logger.info("Signal %s: %s (Confidence %.2f, Composite %.2f)",
                symbol, result.action, result.confidence, result.composite)

    # 5) Alert (nur BUY/SELL; Config aus Watchlist-Eintrag oder
    #    Portfolio-Ableitung mit Defaults)
    alert_enabled, min_confidence = await alert_config(db, symbol)
    if (signal.action in ("BUY", "SELL") and alert_enabled
            and signal.confidence >= min_confidence):
        try:
            from app.services_settings import load_settings
            await dispatch_signal_alert(signal, asset, await load_settings(db, "comm"))
            signal.delivered = True
            await db.commit()
        except Exception as e:
            logger.error("Alert-Versand für %s fehlgeschlagen: %s", symbol, e)

    return signal


async def run_all(db: AsyncSession) -> int:
    symbols = await effective_symbols(db)
    # Effektive LLM-Config sichtbar machen — beantwortet im Log sofort die
    # Frage "warum sehe ich keine LLM-Calls beim Provider?"
    from app.services_settings import load_settings
    llm_cfg = await load_settings(db, "llm")
    logger.info("Analyse-Lauf: %d Symbole | LLM %s @ %s | Modell %s | API-Key %s",
                len(symbols), llm_cfg["provider"], llm_cfg["base_url"],
                llm_cfg["model"], "gesetzt" if llm_cfg["api_key"] else "FEHLT")
    count = 0
    for symbol in symbols:
        try:
            if await run_for_symbol(db, symbol) is not None:
                count += 1
        except Exception as e:
            logger.exception("Pipeline-Fehler für %s: %s", symbol, e)
    return count
