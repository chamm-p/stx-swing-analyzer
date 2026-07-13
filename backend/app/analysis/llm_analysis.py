"""LLM-Analyse-Stufe: News-Sentiment + Asset-Review.

Ergebnisse werden in ``analysis_results`` (Audit/History) und direkt am
Artikel (Sentiment) persistiert. Fehler einzelner Artikel brechen die
Pipeline nicht ab.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm import prompts
from app.llm.client import LLMClient, LLMError
from app.models import AnalysisResult, Asset, NewsArticle, utcnow

logger = logging.getLogger(__name__)


async def analyze_pending_sentiment(db: AsyncSession, llm: LLMClient, symbol: str,
                                    asset_name: str | None) -> int:
    """Bewertet alle noch nicht analysierten Artikel eines Symbols."""
    result = await db.execute(
        select(NewsArticle)
        .where(NewsArticle.symbols.any(symbol), NewsArticle.analyzed_at.is_(None))
        .order_by(NewsArticle.published_at.desc())
        .limit(25)
    )
    articles = result.scalars().all()
    done = 0
    for article in articles:
        try:
            data = await llm.complete_json(
                prompts.SENTIMENT_SYSTEM,
                prompts.SENTIMENT_USER.format(
                    symbol=symbol,
                    name=asset_name or symbol,
                    title=article.title,
                    source=article.source_name or "?",
                    summary=(article.summary or "")[:1500],
                ),
            )
            article.sentiment_score = max(-1.0, min(1.0, float(data.get("score", 0.0))))
            article.sentiment_label = str(data.get("label", "neutral"))[:20]
            article.sentiment_rationale = data.get("rationale")
            article.analyzed_at = utcnow()
            db.add(AnalysisResult(symbol=symbol, kind="sentiment", model=llm.model, payload={
                "article_id": str(article.id), "title": article.title, **data,
            }))
            done += 1
        except LLMError as e:
            # Provider down / Key falsch: betrifft alle weiteren Artikel
            # genauso — Lauf abbrechen statt pro Artikel 3x zu retrien.
            logger.warning("Sentiment-Analyse abgebrochen (%s): LLM nicht verfügbar: %s", symbol, e)
            break
        except (ValueError, TypeError) as e:
            logger.warning("Sentiment-Analyse fehlgeschlagen (%s, %r): %s", symbol, article.title[:60], e)
    await db.commit()
    return done


async def recent_scored_articles(db: AsyncSession, symbol: str, limit: int = 15) -> list[dict]:
    result = await db.execute(
        select(NewsArticle)
        .where(NewsArticle.symbols.any(symbol), NewsArticle.sentiment_score.isnot(None))
        .order_by(NewsArticle.published_at.desc())
        .limit(limit)
    )
    return [{
        "title": a.title,
        "published": a.published_at.strftime("%Y-%m-%d"),
        "source": a.source_name,
        "sentiment_score": a.sentiment_score,
        "relevance": 1.0,
    } for a in result.scalars().all()]


async def asset_review(db: AsyncSession, llm: LLMClient, asset: Asset,
                       indicator_snapshot: dict, articles: list[dict]) -> dict:
    """Gesamteinschätzung (fundamental_score, Begründungstexte) per LLM."""
    indicators_text = "\n".join(f"  {k}: {v}" for k, v in indicator_snapshot.items() if v is not None)
    data = await llm.complete_json(
        prompts.ASSET_REVIEW_SYSTEM,
        prompts.ASSET_REVIEW_USER.format(
            symbol=asset.symbol,
            name=asset.name or asset.symbol,
            last_close=indicator_snapshot.get("close", "?"),
            indicators=indicators_text or "(keine)",
            news_block=prompts.format_news_block(articles),
        ),
    )
    db.add(AnalysisResult(symbol=asset.symbol, kind="asset_review", model=llm.model, payload=data))
    await db.commit()
    return data
