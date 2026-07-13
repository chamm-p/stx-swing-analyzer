"""Auth-Dependency: liefert den (einzigen) angemeldeten User oder 401."""

from dataclasses import dataclass

from fastapi import HTTPException, Request

from app.auth.session import COOKIE_NAME, read_session_token
from app.config import get_settings


@dataclass
class CurrentUser:
    email: str
    name: str | None = None


async def require_user(request: Request) -> CurrentUser:
    settings = get_settings()
    if settings.auth_mode == "none":
        return CurrentUser(email="local@dev", name="Local Dev")

    token = request.cookies.get(COOKIE_NAME)
    data = read_session_token(token) if token else None
    if not data:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    return CurrentUser(email=data["email"], name=data.get("name"))
