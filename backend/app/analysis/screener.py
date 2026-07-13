"""Universum-Screener: rein technischer Scan ALLER Universum-Symbole.

Bewusst ohne LLM (Kosten: ~90 Symbole × mehrere Scans/Tag). Die
LLM-Analyse (News-Sentiment + Review) läuft erst, wenn ein Kandidat auf
die Watchlist genommen wird. Ein Redis-Lock verhindert parallele Scans
(Scheduler + manueller Trigger).
"""

import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.scoring import effective_threshold, get_profile, technical_score
from app.models import ScreenerResult, UniverseSymbol, utcnow
from app.processing.indicators import compute_indicators
from app.services_redis import get_redis
from app.sources import yahoo

logger = logging.getLogger(__name__)

_LOCK_KEY = "screener:running"
_LOCK_TTL = 3600
# Kürzere Initial-Historie als Watchlist-Assets (reicht für SMA200)
_SCAN_HISTORY_DAYS = 450


async def scan_universe(db: AsyncSession) -> int:
    """Scannt das Universum, ersetzt die Screener-Ergebnisse. Liefert Anzahl."""
    r = get_redis()
    if not await r.set(_LOCK_KEY, "1", nx=True, ex=_LOCK_TTL):
        logger.info("Screener-Scan läuft bereits — übersprungen")
        return 0

    try:
        result = await db.execute(
            select(UniverseSymbol).where(UniverseSymbol.active == True)  # noqa: E712
        )
        symbols = result.scalars().all()
        logger.info("Screener-Scan gestartet: %d Symbole", len(symbols))
        run_at = utcnow()
        rows: list[ScreenerResult] = []

        for u in symbols:
            try:
                await yahoo.sync_ohlcv(db, u.symbol, initial_days=_SCAN_HISTORY_DAYS)
                df = await yahoo.load_ohlcv_df(db, u.symbol, days=_SCAN_HISTORY_DAYS)
                if df.empty or len(df) < 60:
                    continue
                snapshot = compute_indicators(df)["snapshot"]
                profile = get_profile("crypto" if u.segment == "CRYPTO" else "stock")
                threshold = effective_threshold(profile)
                tech, components = technical_score(snapshot, profile)
                if tech >= threshold:
                    action = "BUY"
                elif tech <= -threshold:
                    action = "SELL"
                else:
                    action = "HOLD"
                rows.append(ScreenerResult(
                    run_at=run_at, symbol=u.symbol, action=action,
                    technical_score=round(tech, 4), close=snapshot.get("close"),
                    snapshot={**snapshot, "components": components, "profile": profile.name},
                ))
            except Exception as e:
                logger.warning("Screener: %s übersprungen (%s)", u.symbol, e)

        # Alte Läufe ersetzen — die API zeigt immer den letzten Stand.
        await db.execute(delete(ScreenerResult))
        db.add_all(rows)
        await db.commit()
        logger.info("Screener-Scan fertig: %d Symbole bewertet", len(rows))
        return len(rows)
    finally:
        await r.delete(_LOCK_KEY)


async def is_running() -> bool:
    return bool(await get_redis().exists(_LOCK_KEY))
