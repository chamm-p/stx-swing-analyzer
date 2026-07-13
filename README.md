# STX Swing Analyzer

Selbst-gehostete Plattform zur automatisierten Aggregation von Finanz-News und
Börsendaten, LLM-basierter Analyse und **regelbasiertem Signal-Scoring** für
Swing-Trading (Aktien/ETFs, Horizont 3–30 Tage).

> ⚠️ **Keine Anlageberatung.** Die Plattform erzeugt automatisierte Signale
> und Empfehlungen zu Analysezwecken. Keine Order-Ausführung.

## Architektur

```
┌──────────┐   /api-Proxy   ┌──────────┐        ┌─────────────┐
│ Frontend │ ─────────────▶ │ Backend  │ ─────▶ │ TimescaleDB │
│ Next.js  │                │ FastAPI  │        │ (Hypertables│
└──────────┘                └──────────┘        │  + Retention)│
                                 │              └─────────────┘
                            ┌──────────┐        ┌─────────────┐
                            │  Worker  │ ─────▶ │    Redis    │
                            │APScheduler│       │ Cache/State │
                            └──────────┘        └─────────────┘
```

- **Backend** (FastAPI): REST-API, OIDC-Auth (übernommen aus cura_llm)
- **Worker** (APScheduler): periodisches Fetching + Analyse-Pipeline
- **TimescaleDB**: Hypertables für OHLCV & News mit Retention-Policies
- **Redis**: LLM-Antwort-Cache, OIDC-State

### Analyse-Pipeline (pro Watchlist-Symbol)

1. **Data Collection** — Yahoo Finance (yfinance) für Tages-OHLCV, RSS-Feeds
   für News (Retry mit exponentiellem Backoff, Dedupe per URL-Hash)
2. **Indikatoren** — RSI(14, Wilder), MACD(12/26/9), Bollinger(20/2),
   SMA 20/50/200 (pandas, ohne TA-Lib)
3. **LLM-Analyse** — Sentiment pro Artikel + Asset-Review (fundamentale
   Einschätzung, Risiken, Horizont) als strukturiertes JSON; Antworten
   werden per Prompt-Hash in Redis gecacht
4. **Signal-Scoring (regelbasiert, reproduzierbar)** —
   `composite = 0.5·technisch + 0.3·sentiment + 0.2·fundamental`
   (Gewichte per Env konfigurierbar). Composite ≥ Schwelle → BUY,
   ≤ −Schwelle → SELL, sonst HOLD. Indikator-Snapshot und verwendetes
   Profil werden am Signal gespeichert.

   **Scoring-Profile pro Asset-Klasse** (`analysis/scoring.py`,
   `PROFILES`): Krypto handelt 24/7 und volatiler als Aktien — darum
   eigene Parameter:

   | Parameter | Aktien/ETFs | Krypto | Grund |
   |---|---|---|---|
   | RSI überverkauft/-kauft | 30 / 70 | 25 / 75 | RSI erreicht bei Krypto schneller Extremwerte — sonst feuert Mean-Reversion zu früh |
   | MACD-Normierung (hist/close ×) | 100 | 60 | Histogramm ist relativ zum Kurs größer — sonst sättigt die Komponente bei ±1 |
   | BUY/SELL-Schwelle | `SCORE_THRESHOLD` (0.35) | `SCORE_THRESHOLD_CRYPTO` (0.45) | höheres Grundrauschen im 24/7-Markt |

   Das Profil wird automatisch gewählt: im Screener über das
   Universum-Segment, in der Watchlist-Pipeline über den Asset-Typ.
5. **Kursziel & Stop (BUY/SELL)** — deterministische ATR-Zielzone:
   Ziel = Kurs ± 2·ATR(14)·√(Horizont/14), gedeckelt an der jüngsten
   Swing-Marke (60d-Hoch/-Tief); Stop = Kurs ∓ 1.5·ATR → CRV pro Signal.
   Für Aktien/ETFs zusätzlich der **Analysten-Konsens** (Yahoo,
   targetMeanPrice, 24h gecacht) als externer Vergleichswert. Das
   Signal-Review misst, wie oft Ziele im Horizont erreicht wurden
   („Kursziel erreicht"-Quote) — Datenbasis fürs Tuning der Faktoren.
6. **Alerts** — Telegram und/oder E-Mail bei neuen BUY/SELL-Signalen
   inkl. Ziel/Stop/CRV (pro Asset abschaltbar, Confidence-Schwelle
   konfigurierbar)

Signale werden dedupliziert: ein neues Signal entsteht nur bei
Richtungswechsel oder nach Ablauf von `SIGNAL_REFRESH_HOURS`.

### Universum-Screener (Top-Signale)

Unabhängig von Watchlist und Portfolio scannt der Worker alle
`SCAN_INTERVAL_MIN` (default 6h) ein konfigurierbares **Universum**
(~110 Symbole in den Segmenten **US** (66), **DAX** (26) und
**CRYPTO** (Top 20 als Yahoo-Ticker `BTC-USD`, `ETH-USD`, …), per API
erweiterbar) — rein technisch, ohne LLM-Kosten. Die Seite
**Top-Signale** zeigt die Bestenliste nach Signalstärke, filterbar nach
Segment; von dort lassen sich Kandidaten in die Watchlist (→ volle
LLM-Analyse) oder direkt in ein Portfolio übernehmen. Ziel: Kandidaten
außerhalb des eigenen Bias sichtbar machen.

### Portfolios

Beliebig viele Portfolios, je als **echtes** Depot (laufende Positionen
nachbilden) oder **Trial** (Papertrading zum Strategie-Testen).
Positionen mit Stückzahl/Kaufkurs (leer = aktueller Kurs), Verkauf mit
realisiertem P/L, Equity-Kurve aus den Tagesschlusskursen. Symbole aus
offenen Positionen werden automatisch beim Kurs-Sync und bei den
symbolbezogenen News-Feeds mitgetrackt.

### Auto-Portfolio & Signal-Review (Selfimprovement, Stufe 1+2)

Ein Portfolio vom Typ **Auto** handelt die System-Signale selbständig —
als reines Paper-Trading, ohne echtes Geld und ohne Order-Ausführung.
Rahmenbedingungen pro Portfolio: Startkapital, max. Volumen pro Trade,
max. offene Positionen, Mindest-Confidence, optional Screener-BUYs.
Käufe bei BUY-Signalen (Ziel/Stop werden aus dem Signal übernommen bzw.
für Screener-Käufe frisch berechnet); Verkäufe nach Priorität
**Stop-Loss → Take-Profit → SELL-Signal → Horizont-Ablauf** (geprüft am
Tagesschluss); 3-Tage-Wiedereinstiegs-Cooldown gegen
Kauf/Verkauf-Pingpong. Cash und Gesamt-P/L seit Start werden mitgeführt.

Das **Signal-Review** (Seite „Review") bewertet unabhängig davon JEDES
Signal nach Ablauf seines Horizonts gegen die tatsächliche
Kursentwicklung: Trefferquote und Ø-Rendite je Aktion und Asset-Klasse.
Beides zusammen ist die Datengrundlage für späteres Parameter-Tuning —
bewusst OHNE automatische Selbstoptimierung: Parameter-Änderungen
gehören in ein Backtesting mit Walk-Forward-Validierung
(Champion/Challenger, Phase 2), nicht in eine freilaufende
Feedback-Schleife (Überfitting-Gefahr bei kleiner Stichprobe).

### News-Quellen

Default-Feeds (per URL idempotent geseedet, in der UI verwaltbar):
Yahoo Finance, MarketWatch, CNBC, Investing.com, Seeking Alpha,
Handelsblatt, n-tv, tagesschau, Reddit r/stocks + r/wallstreetbets.
Zusätzlich ruft der Worker pro getracktem Symbol den **symbolbezogenen
Yahoo-Feed** ab (`feeds.finance.yahoo.com/rss/2.0/headline?s=…`) —
diese Artikel sind direkt dem Symbol zugeordnet, ohne Keyword-Matching.

## Quick Start

```bash
cp .env.example .env
# .env editieren: POSTGRES_PASSWORD, SECRET_KEY, LLM_* setzen
docker compose up -d --build
```

- Frontend: http://localhost:5800
- API-Docs (Swagger): http://localhost:5800/api/docs — läuft durch den Frontend-Proxy; Backend, DB und Redis sind nicht veröffentlicht

Erster Schritt im UI: unter **Watchlist** Symbole hinzufügen (Yahoo-Notation,
z.B. `AAPL`, `SAP.DE`, `IWDA.AS`). Der Worker lädt dann Kursdaten, ordnet
News zu und erzeugt Signale. „Jetzt analysieren" auf der Asset-Seite stößt
die Pipeline manuell an.

## Konfiguration

**UI-Einstellungen** (Seite „Einstellungen"): LLM (Provider, Base-URL,
API-Key, Modell — mit „Modelle laden" direkt vom Provider als
Verbindungstest — und **Thinking/Reasoning-Steuerung**), E-Mail/SMTP,
Telegram und die News-Datenquellen.

**Reasoning-Modelle (Qwen3.x, o-Serie, …):** Für Analyse-Calls sollte
Thinking abgeschaltet werden — Qwen3.5 auf vLLM antwortete im Test mit
Thinking in ~13.4 s, ohne in ~0.25 s. Modi (Muster aus cura_llm):
`qwen_template` (vLLM `chat_template_kwargs.enable_thinking=false`),
`openai_effort` (`reasoning_effort=minimal`), `disable_field`
(MiniMax-Stil), `none`. Zusätzlich werden `<think>…</think>`-Blöcke
defensiv aus Antworten entfernt (Setups ohne Reasoning-Parser).
UI-Werte liegen in der DB (Secrets Fernet-verschlüsselt mit dem
`SECRET_KEY`), überschreiben die `.env`-Defaults sofort für Backend und
Worker und lassen sich pro Sektion auf die `.env` zurücksetzen.
Dark/Light-Theme ist über den Button im Topmenü umschaltbar.

Alle weiteren Optionen in [.env.example](.env.example). Wichtigste Blöcke:

| Block | Variablen |
|---|---|
| Auth | `AUTH_MODE` (`none`/`oidc`), `OIDC_*`, `ALLOWED_EMAILS` |
| LLM | `LLM_PROVIDER` (`openai`-kompatibel/`anthropic`), `LLM_BASE_URL`, `LLM_MODEL`, `LLM_CACHE_TTL` |
| Scheduler | `FETCH_MARKET_INTERVAL_MIN`, `FETCH_NEWS_INTERVAL_MIN`, `ANALYZE_INTERVAL_MIN` |
| Scoring | `SCORE_WEIGHT_*`, `SCORE_THRESHOLD`, `SIGNAL_REFRESH_HOURS` |
| Retention | `RETENTION_OHLCV_DAYS` (730), `RETENTION_NEWS_DAYS` (365) |
| Alerts | `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`, `SMTP_*`, `ALERT_EMAIL_TO` |

### OIDC (Produktion)

`AUTH_MODE=oidc` setzen und `OIDC_DISCOVERY_URL` (Realm-/Provider-Basis-URL,
`/.well-known/openid-configuration` wird automatisch ergänzt), `OIDC_CLIENT_ID`,
`OIDC_CLIENT_SECRET` und `OIDC_REDIRECT_URI`
(`https://<frontend-host>/api/auth/callback`) konfigurieren. Der Flow ist die
Generic-OIDC-Implementierung aus cura_llm (JWKS-verifizierte ID-Tokens,
State/Nonce-Replay-Schutz via Redis). `ALLOWED_EMAILS` wirkt als
Single-User-Gate.

## MCP-Connector

Das Backend exponiert einen **MCP-Server** (Streamable HTTP, Muster aus
cura-stro) unter `http://<host>:5800/api/mcp` — läuft durch den
Frontend-Proxy, kein zusätzlicher Port. Aktivierung am einfachsten über
**Einstellungen → MCP-Connector**: Token generieren und die fertigen
Snippets (Endpoint, Claude-Code-Befehl, mcp-remote-JSON) kopieren.
Alternativ `MCP_TOKEN` in der `.env` setzen (`openssl rand -hex 24`) —
ein UI-generiertes Token hat Vorrang. Auth per Header `x-stx-token`
oder `Authorization: Bearer <token>`; ohne Token ist der Endpunkt
deaktiviert.

Tools: `get_signals`, `get_top_signals` (Screener, filterbar nach
US/DAX/CRYPTO), `get_watchlist`, `get_asset_analysis` (Indikatoren +
LLM-Review + News-Sentiment), `get_portfolios`, `get_signal_review`
(Trefferquoten), `add_to_watchlist`, `run_analysis`.

Anbindung, z.B. Claude Code:

```bash
claude mcp add --transport http stx http://<host>:5800/api/mcp \
  --header "x-stx-token: <MCP_TOKEN>"
```

Für Clients ohne Streamable-HTTP-Support via `mcp-remote`:

```json
{
  "mcpServers": {
    "stx": {
      "command": "npx",
      "args": ["mcp-remote", "http://<host>:5800/api/mcp",
               "--header", "x-stx-token: <MCP_TOKEN>"]
    }
  }
}
```

## cura_llm-Reuse

| Modul | Herkunft |
|---|---|
| `backend/app/auth/oidc_service.py` | 1:1 aus cura_llm (`services/oidc_service.py`) |
| `backend/app/auth/oidc_config.py` | vereinfachte Env-Variante der cura_llm-Config |
| `backend/app/llm/client.py` | schlanker Neuaufbau nach dem cura_llm-Provider-Muster (OpenAI-kompatibel + Anthropic) |

## Betrieb & Qualität

- **Tests + CI:** pytest-Suite für den deterministischen Kern (Scoring,
  Profile, Hysterese, Zielzonen, Indikatoren, Sentiment-Aggregation,
  JSON-Parsing) unter `backend/tests/`; läuft per GitHub Action bei
  jedem Push (`.github/workflows/ci.yml`), zusammen mit dem
  Frontend-Build als Typecheck. Lokal:
  `docker compose run --rm -v ./backend/tests:/srv/tests --entrypoint sh backend -c "pip install pytest && python -m pytest tests"`
- **Dependencies gepinnt** (`backend/requirements.txt`) — besonders
  yfinance ändert regelmäßig sein Verhalten; Upgrades bewusst machen.
- **Ops-Alarme:** Schlägt der Kurs-Sync für ein Symbol oder eine
  News-Quelle 3 Läufe in Folge fehl, kommt eine Meldung über die
  konfigurierten Alert-Kanäle (Dedupe: max. 1 Meldung/24h je Störung).
- **DB-Backup:** `db-backup`-Service macht täglich einen `pg_dump` nach
  `./backups/` (14 Tage Aufbewahrung). Restore:
  `docker compose exec -T db pg_restore -U stx -d stx --clean < backups/stx-<datum>.dump`
- **Sentiment-Aggregation:** LLM-Relevanz je Artikel × exponentieller
  Zeit-Abkling (Halbwertszeit 5 Tage) — alte News verlieren Gewicht.
- **Portfolio-Benchmark:** Equity-Kurven zeigen `BENCHMARK_SYMBOL`
  (Default SPY, auf Startwert normiert) als Vergleichslinie.

## Roadmap / Phase 2

- Abonnement-Datenquellen (konfigurierbare kostenpflichtige Streams)
- WebPush als dritter Alert-Kanal (VAPID + Service Worker)
- Reddit/X-Connectoren (API-Keys erforderlich)
- Alternativer Kursdaten-Provider als Yahoo-Fallback
- Backtesting-Framework
- Alembic-Migrationen (aktuell `create_all` + idempotente Hypertable-Setups)
