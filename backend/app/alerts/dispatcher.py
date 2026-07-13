"""Push-Alerts für neue Signale: Telegram + E-Mail (je nach Konfiguration).

WebPush ist als dritter Kanal vorgesehen (Phase 2) — erfordert
VAPID-Keys und Subscription-Verwaltung im Frontend.
"""

import asyncio
import logging
import smtplib
from email.mime.text import MIMEText

import httpx

from app.config import get_settings
from app.models import Asset, Signal

logger = logging.getLogger(__name__)


def _format_message(signal: Signal, asset: Asset) -> str:
    emoji = {"BUY": "🟢", "SELL": "🔴"}.get(signal.action, "⚪")
    return (
        f"{emoji} {signal.action}: {asset.symbol} ({asset.name or ''})\n"
        f"Kurs: {signal.price_at_signal} | Confidence: {signal.confidence:.0%} | "
        f"Horizont: ~{signal.horizon_days} Tage\n"
        f"Scores — technisch {signal.technical_score:+.2f}, "
        f"Sentiment {signal.sentiment_score:+.2f}, fundamental {signal.fundamental_score:+.2f}\n\n"
        f"{signal.rationale or ''}\n\n"
        f"⚠️ Automatisch generiertes Signal — keine Anlageberatung."
    )


async def _send_telegram(text: str) -> None:
    s = get_settings()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage",
            json={"chat_id": s.telegram_chat_id, "text": text},
        )
        resp.raise_for_status()


def _send_email_sync(subject: str, body: str) -> None:
    s = get_settings()
    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = s.smtp_from or s.smtp_user
    msg["To"] = s.alert_email_to
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as server:
        server.starttls()
        if s.smtp_user:
            server.login(s.smtp_user, s.smtp_password)
        server.send_message(msg)


async def dispatch_signal_alert(signal: Signal, asset: Asset) -> None:
    """Versendet über alle konfigurierten Kanäle; Fehler einzelner Kanäle
    verhindern die anderen nicht."""
    s = get_settings()
    text = _format_message(signal, asset)
    sent = []

    if s.telegram_bot_token and s.telegram_chat_id:
        try:
            await _send_telegram(text)
            sent.append("telegram")
        except Exception as e:
            logger.error("Telegram-Alert fehlgeschlagen: %s", e)

    if s.smtp_host and s.alert_email_to:
        try:
            subject = f"[stx] {signal.action} {asset.symbol} ({signal.confidence:.0%})"
            await asyncio.to_thread(_send_email_sync, subject, text)
            sent.append("email")
        except Exception as e:
            logger.error("E-Mail-Alert fehlgeschlagen: %s", e)

    if not sent:
        raise RuntimeError("Kein Alert-Kanal konfiguriert oder alle fehlgeschlagen")
    logger.info("Alert für %s %s versendet via %s", signal.action, asset.symbol, ", ".join(sent))
