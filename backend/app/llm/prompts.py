"""Prompt-Templates der Analyse-Pipeline.

Alle Prompts verlangen strukturiertes JSON — der Signal-Scoring-Layer
verarbeitet ausschließlich die numerischen Felder, die Freitexte dienen
der Begründung im UI.
"""

SENTIMENT_SYSTEM = """Du bist ein Finanzanalyst. Du bewertest Nachrichten-Schlagzeilen \
im Hinblick auf ihre kurzfristige Kurswirkung (Swing-Trading, 3-30 Tage) für ein konkretes Wertpapier.
Antworte AUSSCHLIESSLICH mit einem JSON-Objekt, ohne Markdown, ohne Erklärtext davor oder danach."""

SENTIMENT_USER = """Wertpapier: {symbol} ({name})

Nachricht:
Titel: {title}
Quelle: {source}
Zusammenfassung: {summary}

Bewerte die erwartete Kurswirkung dieser Nachricht auf {symbol} im Swing-Trading-Horizont.
Antworte als JSON:
{{
  "score": <float -1.0 (stark negativ) bis 1.0 (stark positiv), 0.0 = neutral/irrelevant>,
  "label": "<bearish|neutral|bullish>",
  "relevance": <float 0.0 bis 1.0, wie relevant die Nachricht für {symbol} ist>,
  "rationale": "<1-2 Sätze Begründung auf Deutsch>"
}}"""

ASSET_REVIEW_SYSTEM = """Du bist ein erfahrener Swing-Trading-Analyst (Horizont 3-30 Tage). \
Du bekommst technische Indikatoren und aktuelle News-Sentiments zu einem Wertpapier und lieferst \
eine strukturierte Einschätzung. Du gibst KEINE Anlageberatung, sondern eine analytische Einordnung.
Antworte AUSSCHLIESSLICH mit einem JSON-Objekt, ohne Markdown, ohne Erklärtext."""

ASSET_REVIEW_USER = """Wertpapier: {symbol} ({name})
Letzter Schlusskurs: {last_close}

Technische Indikatoren (aktuell):
{indicators}

Anstehende Termine (Event-Risiko im Swing-Horizont beachten!):
{events}

News der letzten Tage (mit LLM-Sentiment-Scores):
{news_block}

Erstelle eine Einschätzung für den Swing-Trading-Horizont. Antworte als JSON:
{{
  "fundamental_score": <float -1.0 bis 1.0 — Einschätzung der Nachrichtenlage/fundamentalen Situation>,
  "technical_view": "<2-3 Sätze: Interpretation der Indikatorlage auf Deutsch>",
  "summary": "<3-4 Sätze Gesamteinschätzung auf Deutsch>",
  "key_risks": ["<Risiko 1>", "<Risiko 2>"],
  "suggested_horizon_days": <int 3 bis 30>
}}"""


def format_news_block(articles: list[dict]) -> str:
    if not articles:
        return "(keine aktuellen News vorhanden)"
    lines = []
    for a in articles[:15]:
        lines.append(
            f"- [{a.get('published', '?')}] {a.get('title', '')} "
            f"(Sentiment: {a.get('sentiment_score', 'n/a')}, Quelle: {a.get('source', '?')})"
        )
    return "\n".join(lines)
