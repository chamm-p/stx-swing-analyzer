"""Schlanker LLM-Client nach dem Provider-Muster aus cura_llm.

Unterstützt OpenAI-kompatible Endpoints (OpenAI, vLLM, Ollama,
OpenRouter, ...) und die Anthropic Messages API. Antworten werden —
gekeyt über einen Prompt-Hash — in Redis gecacht, damit wiederholte
Pipeline-Läufe über identischen Daten keine Kosten erzeugen.
"""

import asyncio
import hashlib
import json
import logging
import re

import httpx

from app.config import get_settings
from app.services_redis import get_redis

logger = logging.getLogger(__name__)

_RETRIES = 3
_BACKOFF_BASE = 2.0


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self) -> None:
        s = get_settings()
        self.provider = s.llm_provider
        self.base_url = s.llm_base_url.rstrip("/")
        self.api_key = s.llm_api_key
        self.model = s.llm_model
        self.max_tokens = s.llm_max_tokens
        self.temperature = s.llm_temperature
        self.cache_ttl = s.llm_cache_ttl

    # ------------------------------------------------------------------
    async def complete(self, system: str, user: str) -> str:
        """Eine Completion mit Cache + Retry (exponentielles Backoff)."""
        cache_key = "llm:" + hashlib.sha256(
            f"{self.provider}|{self.model}|{system}|{user}".encode()
        ).hexdigest()

        if self.cache_ttl > 0:
            cached = await get_redis().get(cache_key)
            if cached is not None:
                return cached

        last_err: Exception | None = None
        for attempt in range(_RETRIES):
            try:
                if self.provider == "anthropic":
                    text = await self._anthropic(system, user)
                else:
                    text = await self._openai(system, user)
                if self.cache_ttl > 0:
                    await get_redis().set(cache_key, text, ex=self.cache_ttl)
                return text
            except (httpx.HTTPError, LLMError) as e:
                last_err = e
                wait = _BACKOFF_BASE ** attempt
                logger.warning("LLM-Call fehlgeschlagen (Versuch %d/%d): %s — retry in %.0fs",
                               attempt + 1, _RETRIES, e, wait)
                await asyncio.sleep(wait)
        raise LLMError(f"LLM nicht erreichbar nach {_RETRIES} Versuchen: {last_err}")

    async def complete_json(self, system: str, user: str) -> dict:
        """Completion, deren Antwort als JSON-Objekt geparst wird."""
        text = await self.complete(system, user)
        return _parse_json(text)

    # ------------------------------------------------------------------
    async def _openai(self, system: str, user: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as e:
            raise LLMError(f"Unerwartete OpenAI-Antwortstruktur: {e}")

    async def _anthropic(self, system: str, user: str) -> str:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        base = self.base_url if "anthropic" in self.base_url else "https://api.anthropic.com"
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{base}/v1/messages", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        blocks = data.get("content") or []
        return "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def _parse_json(text: str) -> dict:
    """Robustes JSON-Parsing: Code-Fences entfernen, erstes {...} extrahieren."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    raise LLMError(f"LLM-Antwort ist kein valides JSON: {text[:200]!r}")
