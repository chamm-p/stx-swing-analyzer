"""Discovery-Scan: nächtlicher, rein technischer Breiten-Scan über die
kompletten Börsenverzeichnisse (US: NASDAQ+NYSE ~7000, DE: XETRA ~1500).

Zweck: kleine und unbekannte Werte einfangen, die im kuratierten Universum
nie auftauchen würden. Bewusst OHNE LLM und OHNE OHLCV-Persistenz — die
Kursdaten leben nur im Speicher des Scans, gespeichert werden ausschließlich
die Top-Kandidaten. Liquiditäts-Vorfilter (Mindestkurs + Ø-Tagesumsatz),
damit man aus einem Swing-Trade auch wieder herauskommt.
"""

import asyncio
import logging

import pandas as pd
import yfinance as yf
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.scoring import effective_threshold, get_profile, technical_score
from app.config import get_settings
from app.models import DiscoveryResult, utcnow
from app.processing.indicators import compute_indicators
from app.services_redis import get_redis
from app.sources import exchange_dirs

logger = logging.getLogger(__name__)

_LOCK_KEY = "discovery:running"
_LOCK_TTL = 3 * 3600
_CHUNK = 200
_CHUNK_PAUSE_S = 2.0
_MIN_BARS = 120  # genug Historie für SMA-Trend + stabile Indikatoren


def _score_frame(df: pd.DataFrame, profile, threshold: float) -> dict | None:
    """Ein Symbol bewerten. df: open/high/low/close/volume, ts-Index."""
    df = df.dropna(subset=["close"])
    if len(df) < _MIN_BARS:
        return None
    s = get_settings()
    close = float(df["close"].iloc[-1])
    if close < s.discovery_min_price:
        return None
    turnover = float((df["close"] * df["volume"].fillna(0)).tail(20).mean())
    if turnover < s.discovery_min_turnover:
        return None

    snapshot = compute_indicators(df)["snapshot"]
    tech, components = technical_score(snapshot, profile)
    if tech >= threshold:
        action = "BUY"
    elif tech <= -threshold:
        action = "SELL"
    else:
        action = "HOLD"

    closes = df["close"]
    change_1d = None
    if len(closes) >= 2 and closes.iloc[-2]:
        change_1d = round((close - float(closes.iloc[-2])) / float(closes.iloc[-2]) * 100, 2)
    change_7d = None
    if len(closes) >= 6 and closes.iloc[-6]:
        change_7d = round((close - float(closes.iloc[-6])) / float(closes.iloc[-6]) * 100, 2)

    return {
        "action": action, "technical_score": round(tech, 4), "close": close,
        "change_1d": change_1d, "change_7d": change_7d,
        "avg_turnover": round(turnover),
        "snapshot": {**snapshot, "components": components, "profile": profile.name},
    }


def _download_chunk(symbols: list[str]) -> pd.DataFrame:
    return yf.download(
        tickers=symbols, period="1y", interval="1d", auto_adjust=True,
        group_by="ticker", threads=True, progress=False,
    )


def _extract_symbol(bulk: pd.DataFrame, symbol: str) -> pd.DataFrame | None:
    try:
        sub = bulk[symbol] if isinstance(bulk.columns, pd.MultiIndex) else bulk
    except KeyError:
        return None
    if sub is None or sub.empty:
        return None
    sub = sub.rename(columns={
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    })
    if "close" not in sub.columns:
        return None
    return sub[["open", "high", "low", "close", "volume"]]


async def run_discovery(db: AsyncSession) -> int:
    """Scannt beide Verzeichnisse, ersetzt die Discovery-Ergebnisse."""
    r = get_redis()
    if not await r.set(_LOCK_KEY, "1", nx=True, ex=_LOCK_TTL):
        logger.info("Discovery-Scan läuft bereits — übersprungen")
        return 0

    s = get_settings()
    try:
        regions: list[tuple[str, list[list[str]]]] = []
        wanted = [x.strip().upper() for x in s.discovery_regions.split(",") if x.strip()]
        if "US" in wanted:
            try:
                regions.append(("US", await exchange_dirs.us_symbols()))
            except Exception as e:
                logger.error("US-Verzeichnis nicht ladbar: %s", e)
        if "DE" in wanted:
            try:
                regions.append(("DE", await exchange_dirs.xetra_symbols()))
            except Exception as e:
                logger.error("XETRA-Verzeichnis nicht ladbar: %s", e)
        if not regions:
            raise RuntimeError("Kein Börsenverzeichnis verfügbar")

        profile = get_profile("stock")
        threshold = effective_threshold(profile)
        run_at = utcnow()
        scanned = 0
        top: dict[str, list[DiscoveryResult]] = {}

        for region, entries in regions:
            names = {sym: name for sym, name in entries}
            symbols = list(names)
            logger.info("Discovery %s: %d Kandidaten", region, len(symbols))
            scored: list[DiscoveryResult] = []
            for i in range(0, len(symbols), _CHUNK):
                chunk = symbols[i:i + _CHUNK]
                try:
                    bulk = await asyncio.to_thread(_download_chunk, chunk)
                except Exception as e:
                    logger.warning("Discovery-Chunk %s[%d] fehlgeschlagen: %s", region, i, e)
                    continue
                for sym in chunk:
                    try:
                        sub = _extract_symbol(bulk, sym)
                        if sub is None:
                            continue
                        row = _score_frame(sub, profile, threshold)
                        if row is None:
                            continue
                        scanned += 1
                        scored.append(DiscoveryResult(
                            run_at=run_at, symbol=sym, name=names.get(sym),
                            region=region, **row))
                    except Exception as e:
                        logger.debug("Discovery %s übersprungen: %s", sym, e)
                await asyncio.sleep(_CHUNK_PAUSE_S)
            scored.sort(key=lambda x: abs(x.technical_score), reverse=True)
            top[region] = scored[:s.discovery_top_n]

        rows = [row for region_rows in top.values() for row in region_rows]
        await db.execute(delete(DiscoveryResult))
        db.add_all(rows)
        await db.commit()
        logger.info("Discovery-Scan fertig: %d bewertet, %d gespeichert", scanned, len(rows))
        return len(rows)
    finally:
        await r.delete(_LOCK_KEY)


async def is_running() -> bool:
    return bool(await get_redis().exists(_LOCK_KEY))
