"""Signierte Cookie-Sessions (itsdangerous) — bewusst ohne Server-State."""

from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import get_settings

COOKIE_NAME = "stx_session"

_serializer = URLSafeTimedSerializer(get_settings().secret_key, salt="stx-session")


def create_session_token(email: str, name: str | None = None) -> str:
    return _serializer.dumps({"email": email, "name": name})


def read_session_token(token: str) -> dict | None:
    try:
        return _serializer.loads(token, max_age=get_settings().session_max_age)
    except BadSignature:
        return None
