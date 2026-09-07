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


def _fmt(v) -> str:
    return f"{float(v):.2f}" if v is not None else "—"


def _format_message(signal: Signal, asset: Asset, news: list[dict] | None = None) -> str:
    """Handlungsorientierter Alert: Anlass/Prosa zuerst, News als Beleg,
    Zahlen als kompakte Stütze, Link zum Wert."""
    from app.config import get_settings

    emoji = {"BUY": "🟢", "SELL": "🔴"}.get(signal.action, "⚪")
    base = (get_settings().app_base_url or "").rstrip("/")
    link = f"{base}/asset/{asset.symbol}" if base else ""

    lines = [f"{emoji} {signal.action}: {asset.symbol} — {asset.name or ''}".rstrip(" —")]
    if link:
        lines.append(f"🔗 {link}")
    lines.append("")

    # 1) Anlass / Begründung (das „Warum" zuerst)
    if signal.rationale:
        lines.append(signal.rationale.strip())
        lines.append("")

    # 2) News-Anlass: die relevantesten aktuellen Schlagzeilen
    if news:
        lines.append("📰 Aktuelle News:")
        for a in news[:4]:
            tone = ("🟢" if (a.get("sentiment_score") or 0) > 0.15
                    else "🔴" if (a.get("sentiment_score") or 0) < -0.15 else "⚪")
            lines.append(f"  {tone} {a.get('published', '')} — {a.get('title', '')} "
                         f"[{a.get('source', '')}]")
        lines.append("")
    else:
        lines.append("📰 Keine aktuellen News gefunden — Signal rein technisch.")
        lines.append("")

    # 3) Kompakte Kennzahlen
    lines.append(f"Kurs {_fmt(signal.price_at_signal)} · Confidence {signal.confidence:.0%} "
                 f"· Horizont ~{signal.horizon_days} Tage")
    if signal.target_price:
        tl = (f"Ziel {_fmt(signal.target_price)} · Stop {_fmt(signal.stop_price)} "
              f"· CRV 1:{signal.risk_reward}")
        if signal.analyst_target:
            tl += f" · Analysten {_fmt(signal.analyst_target)}"
        lines.append(tl)
    lines.append(f"Scores: technisch {signal.technical_score:+.2f} · "
                 f"Sentiment {_fmt(signal.sentiment_score)}"
                 + (f" (aus {len(news)} News)" if news else " (keine News)")
                 + f" · fundamental {_fmt(signal.fundamental_score)}")
    lines.append("")
    lines.append("⚠️ Automatisch generiertes Signal — keine Anlageberatung.")
    return "\n".join(lines)


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


async def dispatch_signal_alert(signal: Signal, asset: Asset, comm: dict,
                                news: list[dict] | None = None) -> None:
    """Versendet über alle konfigurierten Kanäle; Fehler einzelner Kanäle
    verhindern die anderen nicht. comm = services_settings.load_settings("comm").
    news = die dem Signal zugrunde liegenden Artikel (für Anlass-Prosa)."""
    text = _format_message(signal, asset, news)
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
