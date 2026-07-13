"""Settings-API: LLM- und Kommunikations-Einstellungen (UI-pflegbar).

Secrets sind Write-Only: GET liefert nur has_<feld>-Flags, leere Felder
beim Speichern behalten den Bestand. POST /settings/llm/models fragt die
verfügbaren Modelle direkt beim Provider ab (dient auch als
Verbindungstest).
"""

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_user
from app.database import get_db
from app.services_settings import load_settings, public_view, save_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


class LlmSettings(BaseModel):
    provider: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None  # leer = Bestand behalten


class CommSettings(BaseModel):
    smtp_host: str | None = None
    smtp_port: str | None = None
    smtp_user: str | None = None
    smtp_from: str | None = None
    alert_email_to: str | None = None
    smtp_password: str | None = None  # leer = Bestand behalten
    telegram_chat_id: str | None = None
    telegram_bot_token: str | None = None  # leer = Bestand behalten


@router.get("/settings")
async def get_all_settings(db: AsyncSession = Depends(get_db)):
    return {
        "llm": await public_view(db, "llm"),
        "comm": await public_view(db, "comm"),
    }


@router.put("/settings/llm")
async def put_llm(payload: LlmSettings, db: AsyncSession = Depends(get_db)):
    if payload.provider and payload.provider not in ("openai", "anthropic"):
        raise HTTPException(status_code=422, detail="provider muss 'openai' oder 'anthropic' sein")
    await save_settings(db, "llm", payload.model_dump(exclude_none=True))
    return await public_view(db, "llm")


@router.put("/settings/comm")
async def put_comm(payload: CommSettings, db: AsyncSession = Depends(get_db)):
    await save_settings(db, "comm", payload.model_dump(exclude_none=True))
    return await public_view(db, "comm")


@router.post("/settings/llm/test")
async def test_llm(payload: LlmSettings, db: AsyncSession = Depends(get_db)):
    """Mini-Completion als Verbindungstest (Cache per Nonce umgangen)."""
    import time

    from app.llm.client import LLMClient, LLMError

    eff = await load_settings(db, "llm")
    merged = {**eff, **{k: v for k, v in payload.model_dump(exclude_none=True).items() if v}}
    client = LLMClient(merged)
    t0 = time.monotonic()
    try:
        reply = await client.complete(
            "Du bist ein Verbindungstest. Antworte mit genau einem Wort.",
            f"Sage 'OK'. (Test-Nonce: {time.time()})",
        )
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {
        "ok": True,
        "model": client.model,
        "latency_ms": int((time.monotonic() - t0) * 1000),
        "reply": reply.strip()[:200],
    }


class CommTest(CommSettings):
    channel: str  # email | telegram


@router.post("/settings/comm/test")
async def test_comm(payload: CommTest, db: AsyncSession = Depends(get_db)):
    """Sendet eine Testnachricht über den gewählten Kanal (Formularwerte
    haben Vorrang, Secrets fallen auf gespeicherte/Env-Werte zurück)."""
    import asyncio

    from app.alerts.dispatcher import send_email_sync, send_telegram

    eff = await load_settings(db, "comm")
    overrides = {k: v for k, v in payload.model_dump(exclude_none=True).items()
                 if v and k != "channel"}
    merged = {**eff, **overrides}
    text = "✅ stx-swing-analyzer — Testnachricht. Dieser Kanal funktioniert."

    try:
        if payload.channel == "telegram":
            if not (merged.get("telegram_bot_token") and merged.get("telegram_chat_id")):
                raise HTTPException(status_code=422, detail="Bot-Token und Chat-ID erforderlich")
            await send_telegram(merged, text)
        elif payload.channel == "email":
            if not (merged.get("smtp_host") and merged.get("alert_email_to")):
                raise HTTPException(status_code=422, detail="SMTP-Host und Alert-Empfänger erforderlich")
            await asyncio.to_thread(send_email_sync, merged, "[stx] Testnachricht", text)
        else:
            raise HTTPException(status_code=422, detail="channel muss 'email' oder 'telegram' sein")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Versand fehlgeschlagen: {e}")
    return {"ok": True, "channel": payload.channel}


@router.get("/settings/mcp")
async def get_mcp(db: AsyncSession = Depends(get_db)):
    """Liefert das effektive MCP-Token im Klartext — anders als andere
    Secrets muss es zum Einrichten externer Adapter kopierbar sein."""
    cfg = await load_settings(db, "mcp")
    return {"token": cfg.get("token") or None}


@router.post("/settings/mcp/generate")
async def generate_mcp_token(db: AsyncSession = Depends(get_db)):
    """Erzeugt ein neues MCP-Token (ersetzt das bisherige sofort)."""
    import secrets

    from app.services_settings import invalidate_mcp_cache

    token = secrets.token_urlsafe(24)
    await save_settings(db, "mcp", {"token": token})
    invalidate_mcp_cache()
    return {"token": token}


@router.delete("/settings/{key}")
async def reset_settings(key: str, db: AsyncSession = Depends(get_db)):
    """Setzt eine Sektion auf die .env-Defaults zurück (löscht Overrides
    inkl. gespeicherter Secrets)."""
    from app.models import AppSetting
    from app.services_settings import SCHEMAS
    if key not in SCHEMAS:
        raise HTTPException(status_code=404, detail="Unbekannte Settings-Sektion")
    row = await db.get(AppSetting, key)
    if row:
        await db.delete(row)
        await db.commit()
    if key == "mcp":
        from app.services_settings import invalidate_mcp_cache
        invalidate_mcp_cache()
    return await public_view(db, key)


@router.post("/settings/llm/models")
async def fetch_models(payload: LlmSettings, db: AsyncSession = Depends(get_db)):
    """Modell-Liste vom Provider. Nutzt Formularwerte, fällt pro Feld auf
    gespeicherte/Env-Werte zurück — so lässt sich vor dem Speichern testen."""
    eff = await load_settings(db, "llm")
    provider = payload.provider or eff["provider"]
    base_url = (payload.base_url or eff["base_url"]).rstrip("/")
    api_key = payload.api_key or eff["api_key"]

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            if provider == "anthropic":
                base = base_url if "anthropic" in base_url else "https://api.anthropic.com"
                resp = await client.get(
                    f"{base}/v1/models",
                    headers={"x-api-key": api_key or "", "anthropic-version": "2023-06-01"},
                )
            else:
                headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
                resp = await client.get(f"{base_url}/models", headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502,
                            detail=f"Provider antwortet mit {e.response.status_code} — API-Key/URL prüfen")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Provider nicht erreichbar: {e}")

    models = sorted(m.get("id") for m in data.get("data", []) if m.get("id"))
    return {"models": models, "provider": provider}
