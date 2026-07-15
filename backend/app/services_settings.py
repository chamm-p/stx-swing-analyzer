"""Runtime-Einstellungen: DB-Overrides über Env-Defaults.

Muster wie cura_llm's oidc_config: Secrets werden Fernet-verschlüsselt
gespeichert (Feld ``<name>_enc``), im Save-Payload heißt „leer" =
„gespeicherten Wert behalten" (Write-Only-Felder im UI). Nicht-Secret-
Felder mit leerem String fallen auf den Env-Default zurück.
"""

from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AppSetting

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        digest = hashlib.sha256(get_settings().secret_key.encode()).digest()
        _fernet = Fernet(base64.urlsafe_b64encode(digest))
    return _fernet


def encrypt_value(value: str) -> str:
    return _get_fernet().encrypt(value.encode()).decode()


def decrypt_value(token: str) -> str | None:
    try:
        return _get_fernet().decrypt(token.encode()).decode()
    except (InvalidToken, Exception):  # SECRET_KEY geändert → Secret neu eingeben
        logger.error("Settings-Secret nicht entschlüsselbar (SECRET_KEY geändert?)")
        return None


# Feld-Definitionen pro Settings-Key: (Env-Default-Attribut, is_secret)
SCHEMAS: dict[str, dict[str, tuple[str, bool]]] = {
    "llm": {
        "provider": ("llm_provider", False),
        "base_url": ("llm_base_url", False),
        "model": ("llm_model", False),
        "reasoning_mode": ("llm_reasoning_mode", False),
        "api_key": ("llm_api_key", True),
    },
    "mcp": {
        "token": ("mcp_token", True),
    },
    "comm": {
        "smtp_host": ("smtp_host", False),
        "smtp_port": ("smtp_port", False),
        "smtp_user": ("smtp_user", False),
        "smtp_from": ("smtp_from", False),
        "alert_email_to": ("alert_email_to", False),
        "smtp_password": ("smtp_password", True),
        "telegram_chat_id": ("telegram_chat_id", False),
        "telegram_bot_token": ("telegram_bot_token", True),
    },
    # IBKR-API-Zugang (Gateway-Socket; Zugangsdaten liegen beim Gateway)
    "ibkr": {
        "host": ("ibkr_host", False),
        "port": ("ibkr_port", False),
        "client_id": ("ibkr_client_id", False),
        "account": ("ibkr_account", False),
        "trading_enabled": ("ibkr_trading_enabled", False),
    },
    # Job-Intervalle — der Worker-Tick gleicht laufend mit dem APScheduler
    # ab (Änderungen greifen ohne Neustart, ≤ 20s)
    "scheduler": {
        "fetch_market_interval_min": ("fetch_market_interval_min", False),
        "fetch_news_interval_min": ("fetch_news_interval_min", False),
        "analyze_interval_min": ("analyze_interval_min", False),
        "scan_interval_min": ("scan_interval_min", False),
        "optimize_interval_days": ("optimize_interval_days", False),
        "optimize_segments": ("optimize_segments", False),
        "universe_refresh_days": ("universe_refresh_days", False),
        "discovery_time": ("discovery_time", False),
    },
}


async def load_settings(db: AsyncSession, key: str) -> dict:
    """Effektive Konfiguration: DB-Override (falls gesetzt), sonst Env."""
    env = get_settings()
    schema = SCHEMAS[key]
    row = await db.get(AppSetting, key)
    stored = (row.value if row else {}) or {}

    out: dict = {}
    for field, (env_attr, is_secret) in schema.items():
        if is_secret:
            enc = stored.get(f"{field}_enc")
            value = decrypt_value(enc) if enc else None
            out[field] = value or getattr(env, env_attr)
        else:
            value = stored.get(field)
            out[field] = value if value not in (None, "") else getattr(env, env_attr)
    return out


async def save_settings(db: AsyncSession, key: str, payload: dict) -> dict:
    """Persistiert Overrides. Leere Secrets behalten den Bestand."""
    schema = SCHEMAS[key]
    row = await db.get(AppSetting, key)
    stored = dict((row.value if row else {}) or {})

    for field, (_env_attr, is_secret) in schema.items():
        if field not in payload:
            continue
        value = payload[field]
        if is_secret:
            if value:  # leer = Bestand behalten
                stored[f"{field}_enc"] = encrypt_value(str(value))
        else:
            stored[field] = "" if value is None else str(value)

    if row is None:
        row = AppSetting(key=key, value=stored)
        db.add(row)
    else:
        row.value = stored
    await db.commit()
    return await load_settings(db, key)


# --- MCP-Token mit Prozess-Cache (Guard läuft bei jedem /api/mcp-Request) ---

_mcp_cache: dict = {"token": None, "ts": 0.0}
_MCP_CACHE_TTL = 30.0


async def current_mcp_token() -> str:
    """Effektives MCP-Token (DB-Override vor Env), 30s gecacht."""
    import time
    now = time.time()
    if _mcp_cache["token"] is None or now - _mcp_cache["ts"] > _MCP_CACHE_TTL:
        from app.database import SessionLocal
        async with SessionLocal() as db:
            cfg = await load_settings(db, "mcp")
        _mcp_cache.update(token=cfg.get("token") or "", ts=now)
    return _mcp_cache["token"]


def invalidate_mcp_cache() -> None:
    _mcp_cache["token"] = None


async def public_view(db: AsyncSession, key: str) -> dict:
    """Effektive Werte OHNE Secret-Klartext (nur has_<feld>-Flags)."""
    eff = await load_settings(db, key)
    out: dict = {}
    for field, (_env_attr, is_secret) in SCHEMAS[key].items():
        if is_secret:
            out[f"has_{field}"] = bool(eff.get(field))
        else:
            out[field] = eff.get(field)
    return out
