"""Job-Registry + Status-Austausch zwischen Worker und API (über Redis).

Der Worker führt die Jobs aus und schreibt Letzter-Lauf/Nächster-Lauf
nach Redis; die API liest den Status und setzt Trigger-Flags für
Ad-hoc-Starts, die der Worker-Tick (alle 20s) aufnimmt. Intervalle
liegen als Runtime-Settings (Kategorie "scheduler") in der DB — der
Tick gleicht laufend mit dem APScheduler ab.
"""

import json
import time

from app.services_redis import get_redis

# job_id -> Anzeige + zugehöriges Intervall-Setting (Kategorie "scheduler").
# unit "min"/"days" = Interval-Job; unit "time" = täglicher Cron (HH:MM UTC).
JOBS: dict[str, dict] = {
    "sync_market": {"label": "Kurs-Sync (Watchlist + Positionen)",
                    "setting": "fetch_market_interval_min", "unit": "min"},
    "sync_news": {"label": "News-Sync (RSS + Symbol-News)",
                  "setting": "fetch_news_interval_min", "unit": "min"},
    "analyze": {"label": "LLM-Analyse Watchlist (+ Auto-Trading)",
                "setting": "analyze_interval_min", "unit": "min"},
    "scan_universe": {"label": "Universum-Screener (Top-Signale)",
                      "setting": "scan_interval_min", "unit": "min"},
    "paper_trading": {"label": "Signal-Review + Paper-Trading (Exits)",
                      "setting": None, "unit": "min", "fixed": 60},
    "discovery": {"label": "Discovery-Breiten-Scan (US + XETRA)",
                  "setting": "discovery_time", "unit": "time"},
    "digest": {"label": "Tägliche Handelsempfehlung (Kauf + Bestands-Review)",
               "setting": "digest_times", "unit": "times"},
    "auto_optimize": {"label": "Segment-Auto-Optimierung (Walk-Forward)",
                      "setting": "optimize_interval_days", "unit": "days"},
    "refresh_universe": {"label": "Index-Mitgliedschaften aktualisieren",
                         "setting": "universe_refresh_days", "unit": "days"},
}

_LAST_KEY = "jobs:last:{}"
_TRIGGER_KEY = "jobs:trigger:{}"
_NEXT_KEY = "jobs:next"
_LOCK_KEY = "jobs:lock:{}"


async def record_run(job_id: str, ok: bool, info: str = "", duration_s: float | None = None) -> None:
    payload = {"ts": time.time(), "ok": ok, "info": info[:500]}
    if duration_s is not None:
        payload["duration_s"] = round(duration_s, 1)
    await get_redis().set(_LAST_KEY.format(job_id), json.dumps(payload))


async def last_runs() -> dict[str, dict]:
    r = get_redis()
    out: dict[str, dict] = {}
    for job_id in JOBS:
        raw = await r.get(_LAST_KEY.format(job_id))
        if raw:
            out[job_id] = json.loads(raw)
    return out


async def request_run(job_id: str) -> None:
    await get_redis().set(_TRIGGER_KEY.format(job_id), "1", ex=3600)


async def pop_trigger(job_id: str) -> bool:
    return bool(await get_redis().getdel(_TRIGGER_KEY.format(job_id)))


async def acquire_lock(job_id: str, ttl: int = 6 * 3600) -> bool:
    return bool(await get_redis().set(_LOCK_KEY.format(job_id), "1", nx=True, ex=ttl))


async def release_lock(job_id: str) -> None:
    await get_redis().delete(_LOCK_KEY.format(job_id))


async def is_locked(job_id: str) -> bool:
    return bool(await get_redis().exists(_LOCK_KEY.format(job_id)))


async def publish_next_runs(next_runs: dict[str, str | None]) -> None:
    await get_redis().set(_NEXT_KEY, json.dumps(next_runs), ex=120)


async def next_runs() -> dict[str, str | None]:
    raw = await get_redis().get(_NEXT_KEY)
    return json.loads(raw) if raw else {}
