"""Ops-Benachrichtigungen: Störungen im Datenbetrieb an den User melden.

Nutzt dieselben Kanäle wie Signal-Alerts (Telegram/E-Mail aus den
Einstellungen). Redis-Dedupe verhindert Alarm-Spam: pro Störungsschlüssel
höchstens eine Meldung je 24h. Fail-soft — eine kaputte Meldung darf nie
den Datenbetrieb stören.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

_DEDUPE_TTL = 86400


async def notify_ops(db, dedupe_key: str, text: str) -> None:
    from app.alerts.dispatcher import send_email_sync, send_telegram
    from app.services_redis import get_redis
    from app.services_settings import load_settings

    r = get_redis()
    if not await r.set(f"ops:{dedupe_key}", "1", nx=True, ex=_DEDUPE_TTL):
        return  # innerhalb 24h schon gemeldet

    logger.warning("OPS-Alarm: %s", text)
    message = f"🔧 stx-swing-analyzer — Betriebsstörung\n\n{text}"
    try:
        comm = await load_settings(db, "comm")
    except Exception:
        return
    if comm.get("telegram_bot_token") and comm.get("telegram_chat_id"):
        try:
            await send_telegram(comm, message)
        except Exception as e:
            logger.error("Ops-Telegram fehlgeschlagen: %s", e)
    if comm.get("smtp_host") and comm.get("alert_email_to"):
        try:
            await asyncio.to_thread(send_email_sync, comm, "[stx] Betriebsstörung", message)
        except Exception as e:
            logger.error("Ops-E-Mail fehlgeschlagen: %s", e)


async def track_failure(db, key: str, error: str, *, threshold: int = 3,
                        subject: str = "Datenquelle") -> None:
    """Zählt aufeinanderfolgende Fehler; ab `threshold` wird gemeldet."""
    from app.services_redis import get_redis

    r = get_redis()
    count = await r.incr(f"opsfail:{key}")
    await r.expire(f"opsfail:{key}", 7 * 86400)
    if count == threshold:
        await notify_ops(db, f"fail:{key}",
                         f"{subject} schlägt seit {threshold} Läufen in Folge fehl.\n"
                         f"Letzter Fehler: {error[:300]}")


async def track_success(key: str) -> None:
    from app.services_redis import get_redis
    await get_redis().delete(f"opsfail:{key}")
