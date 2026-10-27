"""News-Reaktion: Ist die Nachricht schon im Kurs?

Kernfrage aus der Live-Auswertung von Portfolio 14: Sentiment korrelierte
NEGATIV (-0.21) mit dem Ergebnis — wir kaufen offenbar, nachdem die gute
Nachricht bereits eingepreist ist ("buy the rumour, sell the news").

Diese Messung erfasst, wie stark der Kurs seit der relevantesten frischen
Nachricht schon gelaufen ist. Sie wird vorerst NUR protokolliert (im
Signal-Feld `indicators`), nicht ins Scoring eingerechnet — erst messen,
dann entscheiden.
"""

import logging

logger = logging.getLogger(__name__)

_MAX_AGE_DAYS = 7.0


async def news_reaction(db, symbol: str, articles: list[dict],
                        price_now: float | None) -> dict | None:
    """Kursbewegung seit der jüngsten relevanten Nachricht.

    Liefert u.a. `move_since_pct` (Kursänderung seit dem Handelstag VOR
    der News) und `already_priced_in` (Bewegung > 5 %)."""
    if not articles or not price_now:
        return None
    fresh = [a for a in articles
             if (a.get("age_days") is not None and a["age_days"] <= _MAX_AGE_DAYS)]
    if not fresh:
        return None
    # relevanteste frische Nachricht
    top = max(fresh, key=lambda a: (a.get("relevance") or 0, -(a.get("age_days") or 0)))

    from datetime import date as _date

    from sqlalchemy import text
    try:
        # asyncpg braucht ein echtes date-Objekt, keinen ISO-String
        published = _date.fromisoformat(str(top["published"])[:10])
    except (ValueError, TypeError):
        return None
    try:
        row = (await db.execute(text("""
            SELECT close FROM ohlcv
            WHERE symbol = :s AND ts::date <= :d
            ORDER BY ts DESC LIMIT 1
        """), {"s": symbol, "d": published})).first()
    except Exception as e:
        logger.warning("News-Reaktion %s nicht berechenbar: %s", symbol, e)
        return None
    if not row or not row[0]:
        return None

    base = float(row[0])
    move = (price_now - base) / base * 100
    return {
        "headline": top.get("title"),
        "published": top.get("published"),
        "age_days": round(top.get("age_days") or 0, 1),
        "sentiment": top.get("sentiment_score"),
        "price_before": round(base, 4),
        "move_since_pct": round(move, 2),
        "already_priced_in": move > 5.0,
    }
