"""Gemeinsame Helfer für Datenquellen-Connectoren."""

import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def with_retry(fn: Callable[[], Awaitable[T]], *, retries: int = 3, base_wait: float = 2.0,
                     label: str = "fetch") -> T:
    """Führt fn mit exponentiellem Backoff aus (2s, 4s, 8s, ...)."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return await fn()
        except Exception as e:
            last = e
            wait = base_wait * (2 ** attempt)
            logger.warning("%s fehlgeschlagen (Versuch %d/%d): %s — retry in %.0fs",
                           label, attempt + 1, retries, e, wait)
            await asyncio.sleep(wait)
    raise RuntimeError(f"{label} nach {retries} Versuchen fehlgeschlagen: {last}")
