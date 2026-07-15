"""Reddit über die offizielle OAuth-API (Application-only).

Reddit filtert automatisierte RSS-Abrufe zunehmend per Client-
Fingerprint (429 trotz Browser-UA). Die offizielle API ist kostenlos
(100 Anfragen/Min) und stabil: einmalig unter reddit.com/prefs/apps
eine „script"-App anlegen, Client-ID/Secret in den Einstellungen
hinterlegen. Ohne Credentials fällt der Feed-Abruf auf RSS zurück.
"""

import json
import logging
import re

import httpx

from app.services_redis import get_redis

logger = logging.getLogger(__name__)

_UA = "web:stx-swing-analyzer:v1.0 (self-hosted single-user news reader)"
_TOKEN_KEY = "reddit:token"
_SUB_RE = re.compile(r"reddit\.com/r/([A-Za-z0-9_]+)")


def subreddit_from_url(url: str) -> str | None:
    m = _SUB_RE.search(url or "")
    return m.group(1) if m else None


async def _token(client_id: str, client_secret: str) -> str:
    r = get_redis()
    cached = await r.get(_TOKEN_KEY)
    if cached:
        return cached
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": _UA}) as client:
        resp = await client.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=(client_id, client_secret),
            data={"grant_type": "client_credentials"},
        )
        resp.raise_for_status()
        data = resp.json()
    token = data["access_token"]
    await r.set(_TOKEN_KEY, token, ex=max(int(data.get("expires_in", 3600)) - 60, 300))
    return token


async def fetch_subreddit_entries(subreddit: str, client_id: str,
                                  client_secret: str, limit: int = 50) -> list[dict]:
    """Neueste Posts als Artikel-Dicts (title/url/summary/published_ts)."""
    token = await _token(client_id, client_secret)
    async with httpx.AsyncClient(timeout=30, headers={
        "User-Agent": _UA, "Authorization": f"bearer {token}",
    }) as client:
        resp = await client.get(
            f"https://oauth.reddit.com/r/{subreddit}/new",
            params={"limit": min(limit, 100)},
        )
        if resp.status_code == 401:
            # Token abgelaufen/zurückgezogen → einmal frisch versuchen
            await get_redis().delete(_TOKEN_KEY)
            token = await _token(client_id, client_secret)
            resp = await client.get(f"https://oauth.reddit.com/r/{subreddit}/new",
                                    params={"limit": min(limit, 100)},
                                    headers={"Authorization": f"bearer {token}"})
        resp.raise_for_status()
        payload = resp.json()

    entries = []
    for child in payload.get("data", {}).get("children", []):
        d = child.get("data", {})
        title = d.get("title")
        if not title:
            continue
        entries.append({
            "title": title,
            "url": "https://www.reddit.com" + (d.get("permalink") or ""),
            "summary": (d.get("selftext") or "")[:2000] or None,
            "published_ts": d.get("created_utc"),
        })
    logger.info("Reddit r/%s: %d Posts via API", subreddit, len(entries))
    return entries
