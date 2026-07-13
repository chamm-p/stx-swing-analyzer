"""FastAPI-App: API + Auth + MCP. Fetching/Analyse laufen im Worker-Container."""

import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.portfolios import router as portfolio_router
from app.api.review import router as review_router
from app.api.routes import router as api_router
from app.api.screener import router as screener_router
from app.api.settings import router as settings_router
from app.auth.routes import router as auth_router
from app.database import init_db, SessionLocal
from app.mcp_server import mcp as mcp_server
from app.sources.rss import seed_default_sources
from app.sources.universe import seed_universe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# MCP-ASGI-App einmalig bauen; Mount unter /api/mcp, damit der Endpunkt
# durch den Next.js-Proxy erreichbar ist (Backend-Port nicht veröffentlicht).
_mcp_app = mcp_server.streamable_http_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async with SessionLocal() as db:
        await seed_default_sources(db)
        await seed_universe(db)
        from app.analysis.fees import seed_platforms
        await seed_platforms(db)
    # MCP-Session-Manager mitlaufen lassen (Mount führt eigene Lifespans nicht aus).
    async with contextlib.AsyncExitStack() as stack:
        await stack.enter_async_context(mcp_server.session_manager.run())
        yield


# Docs unter /api/*, damit sie durch den Frontend-Proxy erreichbar sind
# (der Backend-Port ist nicht veröffentlicht).
app = FastAPI(
    title="stx-swing-analyzer",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
)


@app.middleware("http")
async def mcp_token_guard(request: Request, call_next):
    """Schützt /api/mcp mit einem statischen Token (Muster aus cura-stro).
    Header: ``x-stx-token`` oder ``Authorization: Bearer <token>``."""
    path = request.url.path
    if path == "/api/mcp" or path.startswith("/api/mcp/"):
        from app.services_settings import current_mcp_token
        token = await current_mcp_token()
        if not token:
            return JSONResponse(
                {"error": "MCP deaktiviert — MCP_TOKEN in der .env setzen."},
                status_code=503,
            )
        provided = request.headers.get("x-stx-token")
        if not provided:
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                provided = auth_header[7:]
        if provided != token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


app.mount("/api/mcp", _mcp_app)

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
