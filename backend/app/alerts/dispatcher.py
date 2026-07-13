"""Push-Alerts für neue Signale: Telegram + E-Mail (je nach Konfiguration).

WebPush ist als dritter Kanal vorgesehen (Phase 2) — erfordert
VAPID-Keys und Subscription-Verwaltung im Frontend.
"""

import asyncio
import logging
import smtplib
from email.mime.text import MIMEText

import httpx

from app.models import Asset, Signal

logger = logging.getLogger(__name__)


def _format_message(signal: Signal, asset: Asset) -> str:
    emoji = {"BUY": "🟢", "SELL": "🔴"}.get(signal.action, "⚪")
    target_line = ""
    if signal.target_price:
        target_line = f"Ziel: {signal.target_price} | Stop: {signal.stop_price} | CRV 1:{signal.risk_reward}"
        if signal.analyst_target:
            target_line += f" | Analysten-Konsens: {signal.analyst_target}"
        target_line += "\n"
    return (
        f"{emoji} {signal.action}: {asset.symbol} ({asset.name or ''})\n"
        f"Kurs: {signal.price_at_signal} | Confidence: {signal.confidence:.0%} | "
        f"Horizont: ~{signal.horizon_days} Tage\n"
        f"{target_line}"
        f"Scores — technisch {signal.technical_score:+.2f}, "
        f"Sentiment {signal.sentiment_score:+.2f}, fundamental {signal.fundamental_score:+.2f}\n\n"
        f"{signal.rationale or ''}\n\n"
        f"⚠️ Automatisch generiertes Signal — keine Anlageberatung."
    )


async def send_telegram(comm: dict, text: str) -> None:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{comm['telegram_bot_token']}/sendMessage",
            json={"chat_id": comm["telegram_chat_id"], "text": text},
        )
        resp.raise_for_status()


def send_email_sync(comm: dict, subject: str, body: str) -> None:
    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = comm.get("smtp_from") or comm.get("smtp_user")
    msg["To"] = comm["alert_email_to"]
    with smtplib.SMTP(comm["smtp_host"], int(comm.get("smtp_port") or 587), timeout=20) as server:
        server.starttls()
        if comm.get("smtp_user"):
            server.login(comm["smtp_user"], comm.get("smtp_password") or "")
        server.send_message(msg)


async def dispatch_signal_alert(signal: Signal, asset: Asset, comm: dict) -> None:
    """Versendet über alle konfigurierten Kanäle; Fehler einzelner Kanäle
    verhindern die anderen nicht. comm = services_settings.load_settings("comm")."""
    text = _format_message(signal, asset)
    sent = []

    if comm.get("telegram_bot_token") and comm.get("telegram_chat_id"):
        try:
            await send_telegram(comm, text)
            sent.append("telegram")
        except Exception as e:
            logger.error("Telegram-Alert fehlgeschlagen: %s", e)

    if comm.get("smtp_host") and comm.get("alert_email_to"):
        try:
            subject = f"[stx] {signal.action} {asset.symbol} ({signal.confidence:.0%})"
            await asyncio.to_thread(send_email_sync, comm, subject, text)
            sent.append("email")
        except Exception as e:
            logger.error("E-Mail-Alert fehlgeschlagen: %s", e)

    if not sent:
        raise RuntimeError("Kein Alert-Kanal konfiguriert oder alle fehlgeschlagen")
    logger.info("Alert für %s %s versendet via %s", signal.action, asset.symbol, ", ".join(sent))
