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


_RATIONALE_PREFIXES = ("Technisch", "Sentiment", "Fundamental", "Ziel", "Analysten")


def _rationale_prose(text: str | None, limit: int = 260) -> str:
    """Nur die Prosa aus der Signal-Begründung (ohne den vorangestellten
    Score-/Zahlen-Dump), auf Satzgrenze gekürzt."""
    if not text:
        return ""
    parts = [p.strip() for p in text.split(" — ")]
    prose = " — ".join(p for p in parts if p and not p.startswith(_RATIONALE_PREFIXES))
    prose = prose.strip()
    if len(prose) <= limit:
        return prose
    cut = prose[:limit]
    dot = cut.rfind(". ")
    return (cut[:dot + 1] if dot > 80 else cut.rstrip() + "…")


def _setup_from_snapshot(snap: dict) -> str:
    """Kurzbeschreibung des technischen Setups eines Screener-Kandidaten —
    damit klar ist, WORAUF der Score beruht (kein LLM, keine News)."""
    bits = []
    close, rsi = snap.get("close"), snap.get("rsi14")
    if rsi is not None:
        bits.append(f"RSI {rsi:.0f}" + (" (überverkauft)" if rsi < 35 else ""))
    sma200, sma50 = snap.get("sma200"), snap.get("sma50")
    if close and sma200:
        bits.append("über SMA200 (Aufwärtstrend)" if close > sma200 else "unter SMA200 (Abwärtstrend)")
    if close and sma50 and sma200 and close < sma50 < sma200:
        bits.append("unter SMA50")
    bb_lo = snap.get("bb_lower")
    if close and bb_lo and close <= bb_lo * 1.01:
        bits.append("am unteren Bollinger-Band")
    mh, mhp = snap.get("macd_hist"), snap.get("macd_hist_prev")
    if mh is not None and mhp is not None and mh > mhp:
        bits.append("MACD dreht auf")
    return ", ".join(bits) if bits else "technisches Kaufsignal"


def _link(symbol: str) -> str:
    base = (get_settings().app_base_url or "").rstrip("/")
    return f"{base}/asset/{symbol}" if base else ""


async def _news_context(db: AsyncSession, symbol: str) -> dict:
    """Relevanteste aktuelle Schlagzeile + News-Zahl/-Stimmung zum Wert."""
    from app.analysis.llm_analysis import recent_scored_articles
    arts = await recent_scored_articles(db, symbol, limit=10)
    if not arts:
        return {"headline": None, "count": 0, "sentiment": None}
    top = max(arts, key=lambda a: (a.get("relevance") or 0, -a.get("age_days", 0)))
    avg = sum((a.get("sentiment_score") or 0) for a in arts) / len(arts)
    return {"headline": f"{top['published']} — {top['title']}",
            "count": len(arts), "sentiment": round(avg, 2)}


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
            # Das „Warum": Prosa aus der Analyse + News-Anlass + Link
            "why": _rationale_prose(sig.rationale),
            "news": await _news_context(db, sig.symbol),
            "link": _link(sig.symbol),
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
                # Screener = rein technisch: Setup beschreiben, ehrlich
                # kennzeichnen, dass keine News-/LLM-Prüfung stattfand
                "why": _setup_from_snapshot(snap),
                "news": None,
                "link": _link(r.symbol),
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
            else:
                # HALTEN begründen: Abstand zu Stop/Ziel + letzte Einschätzung
                bits = []
                if p.stop_price:
                    bits.append(f"Stop {((price - p.stop_price) / price * 100):.1f}% entfernt")
                if p.target_price:
                    bits.append(f"Ziel noch {((p.target_price - price) / price * 100):+.1f}%")
                if last_sig is not None:
                    age = max(0, (now - last_sig.ts).days)
                    bits.append(f"letzte Analyse {last_sig.action} "
                                f"({round(last_sig.confidence * 100)}%, vor {age} T)")
                else:
                    bits.append("noch nie analysiert")
                reason = "; ".join(bits) or "kein Verkaufsgrund"
            reviews.append({
                "portfolio": pf.name, "symbol": p.symbol, "quantity": p.quantity,
                "entry": p.entry_price, "price": price, "pnl_pct": pnl_pct,
                "target": p.target_price, "stop": p.stop_price,
                "verdict": verdict, "reason": reason,
                "why": _rationale_prose(last_sig.rationale, 180) if last_sig else "",
                "news": await _news_context(db, p.symbol),
                "link": _link(p.symbol),
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

    def news_line(n: dict | None) -> str:
        if not n or not n.get("count"):
            return "  📰 keine aktuellen News"
        tone = "🟢" if (n.get("sentiment") or 0) > 0.15 else "🔴" if (n.get("sentiment") or 0) < -0.15 else "⚪"
        return (f"  📰 {n['headline']} {tone} (Stimmung {n['sentiment']:+.2f} "
                f"aus {n['count']} News)")

    def fmt_buy(b: dict) -> list[str]:
        meta = b.get("confidence")
        head = (f"• {b['symbol']} @ {num(b.get('price'))}"
                + (f" ({round(meta * 100)}%)" if meta else f" (Score {b.get('score')})"))
        tz = (f" · Ziel {num(b.get('target'))} / Stop {num(b.get('stop'))}"
              if b.get("target") else "")
        size = b.get("sizing")
        sz = f" → Vorschlag {size['quantity']} Stk. (~{num(size['volume'])})" if size else ""
        out = [head + tz + sz]
        if b.get("why"):
            out.append(f"  ↳ {b['why']}")
        if b.get("source") == "Screener":
            out.append("  📰 rein technisch — noch keine News-/LLM-Prüfung (Analyse im UI anstoßen)")
        else:
            out.append(news_line(b.get("news")))
        if b.get("link"):
            out.append(f"  🔗 {b['link']}")
        return out

    lines.append("")
    lines.append(f"🟢 Kauf-Kandidaten Watchlist ({len(d['buys'])}):")
    if d["buys"]:
        for b in d["buys"]:
            lines += fmt_buy(b)
    else:
        lines.append("• keine frischen BUY-Signale")
    if d["screener_buys"]:
        lines.append("")
        lines.append(f"🔎 Screener-Kandidaten ({len(d['screener_buys'])}):")
        for b in d["screener_buys"]:
            lines += fmt_buy(b)

    lines.append("")
    sells = [r for r in d["reviews"] if r["verdict"] == "VERKAUFEN"]
    checks = [r for r in d["reviews"] if r["verdict"] == "PRÜFEN"]
    holds = [r for r in d["reviews"] if r["verdict"] == "HALTEN"]
    lines.append(f"📂 Bestand: {len(sells)} verkaufen, {len(checks)} prüfen, {len(holds)} halten")
    for r in sells + checks + holds:
        icon = {"VERKAUFEN": "🔴", "PRÜFEN": "🟡"}.get(r["verdict"], "⚪")
        lines.append(f"{icon} {r['verdict']}: {r['symbol']} ({r['portfolio']}) "
                     f"@ {num(r['price'])} ({r['pnl_pct']:+.1f}%) — {r['reason']}")
        if r.get("why"):
            lines.append(f"  ↳ {r['why']}")
        lines.append(news_line(r.get("news")))
        if r.get("link"):
            lines.append(f"  🔗 {r['link']}")
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
