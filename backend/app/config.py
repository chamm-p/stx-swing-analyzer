"""Zentrale Settings (Env-basiert, pydantic-settings)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Infrastruktur
    database_url: str = "postgresql+asyncpg://stx:stx@localhost:5432/stx"
    redis_url: str = "redis://localhost:6379/0"
    secret_key: str = "dev-insecure"

    # Auth
    auth_mode: str = "none"  # none | oidc
    oidc_discovery_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_redirect_uri: str | None = None
    allowed_emails: str = ""
    session_max_age: int = 7 * 24 * 3600

    # LLM
    llm_provider: str = "openai"  # openai (kompatibel) | anthropic
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    # Thinking/Reasoning abschalten (Muster aus cura_llm providers/openai.py):
    # none | qwen_template (Qwen3+vLLM) | openai_effort (o-Serie/GPT-5)
    # | disable_field (MiniMax-Stil)
    llm_reasoning_mode: str = "none"
    llm_cache_ttl: int = 86400
    llm_max_tokens: int = 1500
    # 0 = deterministisch — wichtig gegen Signal-Flattern durch LLM-Varianz
    llm_temperature: float = 0.0

    # Marktdaten
    benchmark_symbol: str = "SPY"  # Vergleichsindex für Portfolio-Kurven

    # Scheduler
    fetch_market_interval_min: int = 60
    fetch_news_interval_min: int = 30
    analyze_interval_min: int = 120
    scan_interval_min: int = 360
    signal_refresh_hours: int = 24

    # Scoring
    score_weight_technical: float = 0.5
    score_weight_sentiment: float = 0.3
    score_weight_fundamental: float = 0.2
    score_threshold: float = 0.35
    # Höhere Schwelle für Krypto — mehr Grundrauschen im 24/7-Markt
    score_threshold_crypto: float = 0.45
    # Hysterese gegen Signal-Flattern: Ein bestehendes BUY/SELL kippt erst
    # zurück auf HOLD, wenn der Composite um diesen Betrag UNTER die
    # Schwelle fällt (Einstieg: >= Schwelle, Ausstieg: < Schwelle - Hysterese)
    signal_hysteresis: float = 0.10

    # Retention
    retention_ohlcv_days: int = 730
    retention_news_days: int = 365

    # MCP-Server: statisches Zugriffs-Token für /api/mcp (leer = deaktiviert)
    mcp_token: str = ""

    # Alerts
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    alert_email_to: str = ""

    @property
    def allowed_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_emails.split(",") if e.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
