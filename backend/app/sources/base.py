"""Gemeinsame Helfer für Datenquellen-Connectoren."""

import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RateLimited(Exception):
    """Quelle drosselt uns (429) — sofort abbrechen statt nachzutreten,
    Retries verlängern die Sperre nur."""

    def __init__(self, wait_seconds: int, message: str = ""):
        super().__init__(message or f"Rate-Limit — Pause {wait_seconds}s")
        self.wait_seconds = wait_seconds


async def with_retry(fn: Callable[[], Awaitable[T]], *, retries: int = 3, base_wait: float = 2.0,
                     label: str = "fetch") -> T:
    """Führt fn mit exponentiellem Backoff aus (2s, 4s, 8s, ...).
    RateLimited wird durchgereicht (kein Retry)."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return await fn()
        except RateLimited:
            raise
        except Exception as e:
            last = e
            wait = base_wait * (2 ** attempt)
            logger.warning("%s fehlgeschlagen (Versuch %d/%d): %s — retry in %.0fs",
                           label, attempt + 1, retries, e, wait)
            await asyncio.sleep(wait)
    raise RuntimeError(f"{label} nach {retries} Versuchen fehlgeschlagen: {last}")
