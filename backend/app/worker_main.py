"""Worker-Entrypoint: initialisiert DB/Seeds und startet den Scheduler.

Beim Start läuft einmal die komplette Kette (Markt → News → Analyse),
damit nach dem ersten Hochfahren sofort Daten vorhanden sind.
"""

import asyncio
import logging

from sqlalchemy import select

from app.database import SessionLocal, init_db
from app.models import ScreenerResult
from app.scheduler import (
    build_scheduler, job_analyze, job_scan_universe, job_sync_market, job_sync_news,
)
from app.sources.rss import seed_default_sources
from app.sources.universe import seed_universe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("worker")


async def main() -> None:
    await init_db()
    async with SessionLocal() as db:
        await seed_default_sources(db)
        await seed_universe(db)
        from app.analysis.fees import seed_platforms
        await seed_platforms(db)
        has_scan = await db.scalar(select(ScreenerResult.id).limit(1))

    scheduler = build_scheduler()
    scheduler.start()
    logger.info("Scheduler gestartet — Initial-Lauf beginnt")

    await job_sync_market()
    await job_sync_news()
    await job_analyze()
    if has_scan is None:
        # Erster Start: Universum initial scannen (dauert einige Minuten)
        await job_scan_universe()
    logger.info("Initial-Lauf abgeschlossen")

    await asyncio.Event().wait()  # für immer laufen


if __name__ == "__main__":
    asyncio.run(main())
