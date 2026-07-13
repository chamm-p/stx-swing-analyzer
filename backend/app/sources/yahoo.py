"""Yahoo-Finance-Connector (yfinance) — Tages-OHLCV für Swing-Trading.

yfinance ist synchron → Ausführung im Thread-Executor, damit der
Event-Loop nicht blockiert.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Asset, Ohlcv
from app.sources.base import with_retry

logger = logging.getLogger(__name__)

INITIAL_HISTORY_DAYS = 730  # 2 Jahre — genug für SMA200 + Kontext


def _fetch_history_sync(symbol: str, start: datetime | None, initial_days: int) -> pd.DataFrame:
    ticker = yf.Ticker(symbol)
    if start is not None:
        df = ticker.history(start=start.strftime("%Y-%m-%d"), interval="1d", auto_adjust=True)
    else:
        df = ticker.history(period=f"{initial_days}d", interval="1d", auto_adjust=True)
    return df


def _fetch_info_sync(symbol: str) -> dict:
    try:
        return yf.Ticker(symbol).info or {}
    except Exception:
        return {}


async def fetch_asset_info(symbol: str) -> dict:
    """Stammdaten (Name, Währung, Börse, Typ) für die Asset-Anlage."""
    info = await asyncio.to_thread(_fetch_info_sync, symbol)
    quote_type = (info.get("quoteType") or "").lower()
    if quote_type == "etf":
        asset_type = "etf"
    elif quote_type == "cryptocurrency":
        asset_type = "crypto"
    else:
        asset_type = "stock"
    return {
        "name": info.get("longName") or info.get("shortName") or symbol,
        "currency": info.get("currency"),
        "exchange": info.get("exchange"),
        "asset_type": asset_type,
    }


async def sync_ohlcv(db: AsyncSession, symbol: str, initial_days: int = INITIAL_HISTORY_DAYS) -> int:
    """Holt fehlende Tageskerzen und upsertet sie. Liefert Anzahl Zeilen."""
    last_ts = await db.scalar(select(func.max(Ohlcv.ts)).where(Ohlcv.symbol == symbol))
    start = None
    if last_ts is not None:
        # Letzte Kerze erneut laden (kann sich intraday noch ändern)
        start = last_ts - timedelta(days=1)

    df = await with_retry(
        lambda: asyncio.to_thread(_fetch_history_sync, symbol, start, initial_days),
        label=f"yahoo:{symbol}",
    )
    if df is None or df.empty:
        logger.info("Keine neuen Kursdaten für %s", symbol)
        return 0

    rows = []
    for ts, row in df.iterrows():
        if pd.isna(row.get("Close")):
            continue
        # Bar-Datum normalisieren: yfinance liefert Mitternacht in Börsen-
        # Zeitzone; naive UTC-Konvertierung würde EU-Bars auf den Vortag
        # schieben. Kalenderdatum der Kerze als Mitternacht UTC speichern.
        ts_utc = datetime(ts.year, ts.month, ts.day, tzinfo=timezone.utc)
        rows.append({
            "symbol": symbol,
            "ts": ts_utc,
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": int(row["Volume"]) if not pd.isna(row.get("Volume")) else None,
            "source": "yahoo",
        })
    if not rows:
        return 0

    stmt = pg_insert(Ohlcv).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol", "ts"],
        set_={c: stmt.excluded[c] for c in ("open", "high", "low", "close", "volume", "source")},
    )
    await db.execute(stmt)
    await db.commit()
    logger.info("OHLCV-Sync %s: %d Zeilen", symbol, len(rows))
    return len(rows)


async def load_ohlcv_df(db: AsyncSession, symbol: str, days: int = 400) -> pd.DataFrame:
    """Lädt Kursdaten als DataFrame (aufsteigend nach ts) für Indikatoren/Charts."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(Ohlcv).where(Ohlcv.symbol == symbol, Ohlcv.ts >= since).order_by(Ohlcv.ts)
    )
    records = result.scalars().all()
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame([{
        "ts": r.ts, "open": r.open, "high": r.high, "low": r.low,
        "close": r.close, "volume": r.volume,
    } for r in records]).set_index("ts")
    return df


PROFILE_FIELDS = {
    # Stammdaten
    "sector": "sector", "industry": "industry",
    "employees": "fullTimeEmployees", "website": "website",
    "city": "city", "country": "country",
    "summary": "longBusinessSummary",
    # Kennzahlen
    "market_cap": "marketCap", "trailing_pe": "trailingPE",
    "forward_pe": "forwardPE", "dividend_yield": "dividendYield",
    "beta": "beta", "fifty_two_week_high": "fiftyTwoWeekHigh",
    "fifty_two_week_low": "fiftyTwoWeekLow", "total_revenue": "totalRevenue",
    "profit_margin": "profitMargins", "currency": "currency",
}


async def fetch_profile(symbol: str) -> dict:
    """Unternehmensprofil (Yahoo-Stammdaten + Kennzahlen), kuratiert."""
    info = await asyncio.to_thread(_fetch_info_sync, symbol)
    profile = {key: info.get(src) for key, src in PROFILE_FIELDS.items()}
    profile["name"] = info.get("longName") or info.get("shortName") or symbol
    return profile


async def fetch_analyst_targets(symbol: str) -> dict:
    """Analysten-Konsensziele (Yahoo) — Redis-gecacht (24h), fail-soft leer.

    Für Krypto liefert Yahoo keine Analystenziele → leeres dict."""
    import json

    from app.services_redis import get_redis

    r = get_redis()
    cache_key = f"analyst:{symbol}"
    cached = await r.get(cache_key)
    if cached is not None:
        return json.loads(cached)

    try:
        info = await asyncio.to_thread(_fetch_info_sync, symbol)
    except Exception:
        info = {}
    out = {
        "mean": info.get("targetMeanPrice"),
        "high": info.get("targetHighPrice"),
        "low": info.get("targetLowPrice"),
        "count": info.get("numberOfAnalystOpinions"),
    }
    await r.set(cache_key, json.dumps(out), ex=86400)
    return out


async def latest_close(db: AsyncSession, symbol: str) -> float | None:
    result = await db.execute(
        select(Ohlcv.close).where(Ohlcv.symbol == symbol).order_by(Ohlcv.ts.desc()).limit(1)
    )
    return result.scalar()


async def ensure_asset(db: AsyncSession, symbol: str) -> Asset:
    """Legt das Asset an (inkl. Yahoo-Stammdaten), falls es fehlt."""
    asset = await db.get(Asset, symbol)
    if asset:
        return asset
    info = await fetch_asset_info(symbol)
    # Keywords fürs News-Matching: Symbol, voller Name und erstes
    # Namenswort ("Apple" statt nur "Apple Inc.")
    keywords = [symbol]
    name = info.get("name")
    if name and name != symbol:
        keywords.append(name)
        first_word = name.split()[0]
        if len(first_word) > 3 and first_word not in keywords:
            keywords.append(first_word)
    asset = Asset(
        symbol=symbol,
        name=name,
        currency=info["currency"],
        exchange=info["exchange"],
        asset_type=info["asset_type"],
        keywords=keywords,
    )
    db.add(asset)
    await db.commit()
    return asset
