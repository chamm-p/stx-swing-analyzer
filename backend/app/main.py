"""FastAPI-App: API + Auth. Fetching/Analyse laufen im Worker-Container."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.portfolios import router as portfolio_router
from app.api.review import router as review_router
from app.api.routes import router as api_router
from app.api.screener import router as screener_router
from app.api.settings import router as settings_router
from app.auth.routes import router as auth_router
from app.database import init_db, SessionLocal
from app.sources.rss import seed_default_sources
from app.sources.universe import seed_universe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async with SessionLocal() as db:
        await seed_default_sources(db)
        await seed_universe(db)
    yield


app = FastAPI(title="stx-swing-analyzer", lifespan=lifespan)

# Auth unter /api/auth, damit der Next.js-Proxy (/api/*) alles abdeckt.
app.include_router(auth_router, prefix="/api")
app.include_router(api_router)
app.include_router(portfolio_router)
app.include_router(review_router)
app.include_router(screener_router)
app.include_router(settings_router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
