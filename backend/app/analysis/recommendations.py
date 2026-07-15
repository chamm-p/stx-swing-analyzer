"""Tägliche Handelsempfehlung (Digest) — 2× nach Handelsschluss (EU/US).

Zwei Teile, beide ehrlich und regelbasiert:
1. Kauf-Kandidaten: frische BUY-Signale (Watchlist) + stärkste Screener-
   BUYs, jeweils mit Stückzahl-Vorschlag nach der 1%-Regel gegen das
   Referenz-Portfolio (erstes „echtes" Portfolio mit Cash-Führung).
2. Bestands-Review: jede offene Position (real/trial) mit klarer
   Einschätzung — VERKAUFEN (Stop/Ziel erreicht, SELL-Signal),
   PRÜFEN (Horizont abgelaufen) oder HALTEN.

Auto-Portfolios reviewen sich selbst (Auto-Trader) und tauchen hier
nicht auf. Versand über die konfigurierten Alert-Kanäle.
"""

import logging
from datetime import timedelta

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.position_sizing import portfolio_market_value, risk_based_quantity
from app.config import get_settings
from app.models import Portfolio, Position, ScreenerResult, Signal, utcnow
from app.sources.yahoo import latest_close

logger = logging.getLogger(__name__)

_MAX_BUYS = 5
_MAX_SCREENER = 5


async def _reference_portfolio(db: AsyncSession) -> Portfolio | None:
    """Erstes echtes Portfolio mit Cash-Führung — Basis für Stückzahlen."""
    result = await db.execute(
        select(Portfolio).where(Portfolio.kind == "real").order_by(Portfolio.created_at))
    for pf in result.scalars().all():
        if (pf.config or {}).get("start_capital") or pf.cash:
            return pf
    return None


def _size_hint(total_value: float | None, cash: float | None, price: float | None,
               stop: float | None, risk_pct: float) -> dict | None:
    if not total_value or not price:
        return None
    qty = risk_based_quantity(total_value, price, stop, risk_pct)
    if qty is None:
        return None
    if cash is not None:
        qty = min(qty, max(cash, 0) / price)
    qty = int(qty) if qty >= 1 else round(qty, 4)
    if not qty:
        return None
    return {"quantity": qty, "volume": round(qty * price, 2)}


async def build_digest(db: AsyncSession) -> dict:
    s = get_settings()
    now = utcnow()
    ref = await _reference_portfolio(db)
    total_value = cash = None
    if ref is not None:
        open_ref = (await db.execute(
            select(Position).where(Position.portfolio_id == ref.id,
                                   Position.exit_date.is_(None)))).scalars().all()
        total_value = await portfolio_market_value(db, ref, open_ref)
        cash = ref.cash

    # --- 1) Frische BUY-Signale (letzte 24h, bestes je Symbol) ------------
    result = await db.execute(
        select(Signal).where(Signal.action == "BUY",
                             Signal.ts >= now - timedelta(hours=24))
        .order_by(desc(Signal.confidence)))
    buys, seen = [], set()
    for sig in result.scalars().all():
        if sig.symbol in seen:
            continue
        seen.add(sig.symbol)
        buys.append({
            "symbol": sig.symbol, "confidence": sig.confidence,
            "price": sig.price_at_signal, "target": sig.target_price,
            "stop": sig.stop_price, "crv": sig.risk_reward,
            "source": "Signal",
            "sizing": _size_hint(total_value, cash, sig.price_at_signal,
                                 sig.stop_price, s.risk_per_trade_pct),
        })
        if len(buys) >= _MAX_BUYS:
            break

    # --- 2) Stärkste Screener-BUYs außerhalb der Watchlist ---------------
    from sqlalchemy import func
    last_run = await db.scalar(select(func.max(ScreenerResult.run_at)))
    screener_buys = []
    if last_run is not None:
        rows = (await db.execute(
            select(ScreenerResult)
            .where(ScreenerResult.run_at == last_run, ScreenerResult.action == "BUY")
            .order_by(desc(ScreenerResult.technical_score))
            .limit(_MAX_SCREENER * 2))).scalars().all()
        for r in rows:
            if r.symbol in seen:
                continue
            snap = r.snapshot or {}
            screener_buys.append({
                "symbol": r.symbol, "score": r.technical_score,
                "price": r.close, "target": snap.get("target_price"),
                "stop": snap.get("stop_price"), "crv": snap.get("risk_reward"),
                "source": "Screener",
                "sizing": _size_hint(total_value, cash, r.close,
                                     snap.get("stop_price"), s.risk_per_trade_pct),
            })
            if len(screener_buys) >= _MAX_SCREENER:
                break

    # --- 3) Bestands-Review (real + trial) --------------------------------
    reviews = []
    portfolios = (await db.execute(
        select(Portfolio).where(Portfolio.kind.in_(("real", "trial"))))).scalars().all()
    for pf in portfolios:
        open_pos = (await db.execute(
            select(Position).where(Position.portfolio_id == pf.id,
                                   Position.exit_date.is_(None)))).scalars().all()
        for p in open_pos:
            price = await latest_close(db, p.symbol)
            if price is None:
                continue
            pnl_pct = round((price - p.entry_price) / p.entry_price * 100, 2)
            last_sig = await db.scalar(
                select(Signal).where(Signal.symbol == p.symbol)
                .order_by(desc(Signal.ts)).limit(1))
            verdict, reason = "HALTEN", ""
            if p.stop_price and price <= p.stop_price:
                verdict, reason = "VERKAUFEN", f"Stop {p.stop_price} erreicht"
            elif p.target_price and price >= p.target_price:
                verdict, reason = "VERKAUFEN", f"Ziel {p.target_price} erreicht — Gewinn sichern"
            elif last_sig is not None and last_sig.action == "SELL":
                verdict, reason = "VERKAUFEN", f"SELL-Signal ({round(last_sig.confidence * 100)}%)"
            elif p.horizon_days and p.entry_date and \
                    now - p.entry_date > timedelta(days=p.horizon_days):
                verdict, reason = "PRÜFEN", f"Horizont ({p.horizon_days}d) abgelaufen"
            reviews.append({
                "portfolio": pf.name, "symbol": p.symbol, "quantity": p.quantity,
                "entry": p.entry_price, "price": price, "pnl_pct": pnl_pct,
                "target": p.target_price, "stop": p.stop_price,
                "verdict": verdict, "reason": reason,
            })

    return {"ts": now.isoformat(), "reference_portfolio": ref.name if ref else None,
            "portfolio_value": round(total_value, 2) if total_value else None,
            "buys": buys, "screener_buys": screener_buys, "reviews": reviews}


def render_digest(d: dict) -> str:
    lines = ["📬 Handelsempfehlung"]
    if d.get("reference_portfolio"):
        lines.append(f"(Stückzahlen: 1%-Regel auf „{d['reference_portfolio']}“, "
                     f"Wert {d.get('portfolio_value')})")

    def num(v) -> str:
        return f"{v:,.2f}".replace(",", "'") if isinstance(v, (int, float)) else "—"

    def fmt_buy(b: dict) -> str:
        meta = b.get("confidence")
        head = (f"• {b['symbol']} @ {num(b.get('price'))}"
                + (f" ({round(meta * 100)}%)" if meta else f" (Score {b.get('score')})"))
        tz = (f" Ziel {num(b.get('target'))} / Stop {num(b.get('stop'))}"
              if b.get("target") else "")
        size = b.get("sizing")
        sz = f" → Vorschlag {size['quantity']} Stk. (~{num(size['volume'])})" if size else ""
        return head + tz + sz

    lines.append("")
    lines.append(f"🟢 Kauf-Kandidaten Watchlist ({len(d['buys'])}):")
    lines += [fmt_buy(b) for b in d["buys"]] or ["• keine frischen BUY-Signale"]
    if d["screener_buys"]:
        lines.append(f"🔎 Screener-Kandidaten ({len(d['screener_buys'])}):")
        lines += [fmt_buy(b) for b in d["screener_buys"]]

    lines.append("")
    sells = [r for r in d["reviews"] if r["verdict"] == "VERKAUFEN"]
    checks = [r for r in d["reviews"] if r["verdict"] == "PRÜFEN"]
    holds = [r for r in d["reviews"] if r["verdict"] == "HALTEN"]
    lines.append(f"📂 Bestand: {len(sells)} verkaufen, {len(checks)} prüfen, {len(holds)} halten")
    for r in sells + checks:
        icon = "🔴" if r["verdict"] == "VERKAUFEN" else "🟡"
        lines.append(f"{icon} {r['verdict']}: {r['symbol']} ({r['portfolio']}) "
                     f"@ {num(r['price'])} ({r['pnl_pct']:+.1f}%) — {r['reason']}")
    for r in holds:
        lines.append(f"⚪ HALTEN: {r['symbol']} ({r['portfolio']}) "
                     f"@ {num(r['price'])} ({r['pnl_pct']:+.1f}%)")
    return "\n".join(lines)


async def send_digest(db: AsyncSession) -> str:
    """Digest bauen und über die Alert-Kanäle versenden. Liefert Kurzinfo."""
    import asyncio

    from app.alerts.dispatcher import send_email_sync, send_telegram
    from app.services_settings import load_settings

    data = await build_digest(db)
    text = render_digest(data)
    comm = await load_settings(db, "comm")
    sent = []
    if comm.get("telegram_bot_token") and comm.get("telegram_chat_id"):
        try:
            await send_telegram(comm, text)
            sent.append("telegram")
        except Exception as e:
            logger.error("Digest-Telegram fehlgeschlagen: %s", e)
    if comm.get("smtp_host") and comm.get("alert_email_to"):
        try:
            await asyncio.to_thread(send_email_sync, comm, "[stx] Handelsempfehlung", text)
            sent.append("email")
        except Exception as e:
            logger.error("Digest-E-Mail fehlgeschlagen: %s", e)

    # Letzten Digest für die API aufbewahren (24h)
    import json

    from app.services_redis import get_redis
    await get_redis().set("digest:latest", json.dumps({"text": text, **data}), ex=86400)
    info = (f"{len(data['buys'])} Signale, {len(data['screener_buys'])} Screener, "
            f"{len(data['reviews'])} Positionen → {', '.join(sent) or 'keine Kanäle'}")
    logger.info("Digest versendet: %s", info)
    return info
