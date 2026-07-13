"""SQLAlchemy-Modelle.

Hypertables (ohlcv, news_articles) partitionieren über die Zeitspalte —
deren Primärschlüssel MUSS die Zeitspalte enthalten (Timescale-Anforderung).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer,
    String, Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class AppSetting(Base):
    """Runtime-Einstellungen (UI-pflegbar). Überschreiben die Env-Defaults;
    Secrets liegen Fernet-verschlüsselt in ``value`` (Suffix ``_enc``)."""
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Asset(Base):
    __tablename__ = "assets"

    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(200))
    asset_type: Mapped[str] = mapped_column(String(10), default="stock")  # stock | etf
    currency: Mapped[str | None] = mapped_column(String(10))
    exchange: Mapped[str | None] = mapped_column(String(50))
    # Freitext-Aliasse für News-Matching (z.B. "Apple", "AAPL")
    keywords: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Ohlcv(Base):
    __tablename__ = "ohlcv"

    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(30), default="yahoo")

    __table_args__ = (Index("ix_ohlcv_symbol_ts", "symbol", "ts"),)


class NewsArticle(Base):
    __tablename__ = "news_articles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    source_id: Mapped[int | None] = mapped_column(Integer)
    source_name: Mapped[str | None] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    url_hash: Mapped[str] = mapped_column(String(64), index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Zuordnung zu Watchlist-Symbolen (Keyword-Matching beim Fetch)
    symbols: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    # LLM-Sentiment (wird von der Analyse-Pipeline befüllt)
    sentiment_score: Mapped[float | None] = mapped_column(Float)  # -1..1
    sentiment_label: Mapped[str | None] = mapped_column(String(20))
    sentiment_rationale: Mapped[str | None] = mapped_column(Text)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(20), default="news_rss")  # news_rss | market | custom
    name: Mapped[str] = mapped_column(String(100))
    url: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict | None] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    poll_interval_minutes: Mapped[int | None] = mapped_column(Integer)
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WatchlistItem(Base):
    __tablename__ = "watchlist"

    symbol: Mapped[str] = mapped_column(
        String(20), ForeignKey("assets.symbol", ondelete="CASCADE"), primary_key=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    notes: Mapped[str | None] = mapped_column(Text)
    alert_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Alerts nur ab dieser Confidence ausliefern
    min_confidence: Mapped[float] = mapped_column(Float, default=0.5)


class Portfolio(Base):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(10), default="real")  # real | trial | auto
    # Nur kind=auto: Cash-Bestand (Paper) und Trading-Rahmenbedingungen
    # (start_capital, max_per_trade, max_positions, min_confidence,
    #  use_screener, enabled)
    cash: Mapped[float] = mapped_column(Float, default=0.0)
    config: Mapped[dict | None] = mapped_column(JSONB)
    # Offene Positionen automatisch wie Watchlist-Einträge behandeln
    # (Analyse-Pipeline, Dashboard, abgeleitete Watchlist-Anzeige)
    watch_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    portfolio_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("portfolios.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    quantity: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    entry_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    exit_price: Mapped[float | None] = mapped_column(Float)
    exit_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    # Auto-Trading: Herkunft + auslösendes Signal + geplanter Horizont
    source: Mapped[str] = mapped_column(String(10), default="manual")  # manual | auto
    signal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    horizon_days: Mapped[int | None] = mapped_column(Integer)
    # Take-Profit/Stop-Loss für Auto-Positionen (aus dem Signal übernommen)
    target_price: Mapped[float | None] = mapped_column(Float)
    stop_price: Mapped[float | None] = mapped_column(Float)


class UniverseSymbol(Base):
    """Screening-Universum — unabhängig von Watchlist/Portfolio, gegen Bias."""
    __tablename__ = "universe"

    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(200))
    segment: Mapped[str | None] = mapped_column(String(30))  # z.B. DAX, US
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ScreenerResult(Base):
    """Ergebnis eines Universum-Scans (rein technisch, ohne LLM)."""
    __tablename__ = "screener_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    action: Mapped[str] = mapped_column(String(10))  # BUY | SELL | HOLD
    technical_score: Mapped[float] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    snapshot: Mapped[dict | None] = mapped_column(JSONB)


class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(30))  # sentiment | asset_review
    model: Mapped[str | None] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSONB)


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    action: Mapped[str] = mapped_column(String(10))  # BUY | SELL | HOLD
    confidence: Mapped[float] = mapped_column(Float)  # 0..1
    composite_score: Mapped[float] = mapped_column(Float)  # -1..1
    technical_score: Mapped[float | None] = mapped_column(Float)
    sentiment_score: Mapped[float | None] = mapped_column(Float)
    fundamental_score: Mapped[float | None] = mapped_column(Float)
    rationale: Mapped[str | None] = mapped_column(Text)
    horizon_days: Mapped[int] = mapped_column(Integer, default=14)
    # Indikator-Snapshot zum Signalzeitpunkt (Reproduzierbarkeit)
    indicators: Mapped[dict | None] = mapped_column(JSONB)
    price_at_signal: Mapped[float | None] = mapped_column(Float)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    # Kursziel/Stop (ATR-Zielzone) + Analysten-Konsens (Yahoo, nur Aktien)
    target_price: Mapped[float | None] = mapped_column(Float)
    stop_price: Mapped[float | None] = mapped_column(Float)
    risk_reward: Mapped[float | None] = mapped_column(Float)
    analyst_target: Mapped[float | None] = mapped_column(Float)
    analyst_count: Mapped[int | None] = mapped_column(Integer)
    # Signal-Review: Auswertung nach Ablauf des Horizonts
    eval_price: Mapped[float | None] = mapped_column(Float)
    eval_return_pct: Mapped[float | None] = mapped_column(Float)
    eval_hit: Mapped[bool | None] = mapped_column(Boolean)  # nur BUY/SELL
    eval_target_hit: Mapped[bool | None] = mapped_column(Boolean)  # Ziel im Horizont erreicht?
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
