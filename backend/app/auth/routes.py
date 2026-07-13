"""OIDC-Login-Flow: /auth/login → IdP → /auth/callback → Session-Cookie.

State + Nonce werden mit kurzem TTL in Redis gehalten (Replay-Schutz).
"""

import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from urllib.parse import urlencode, urlparse

from app.auth import oidc_config
from app.auth.deps import CurrentUser, require_user
from app.auth.oidc_service import OidcError, OidcSvc, _get_discovery
from app.auth.session import COOKIE_NAME, create_session_token
from app.config import get_settings
from app.services_redis import get_redis

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

_STATE_TTL = 600  # 10 min


@router.get("/login")
async def login():
    cfg = oidc_config.load_config()
    if not cfg.is_usable:
        raise HTTPException(status_code=400, detail="OIDC ist nicht konfiguriert (AUTH_MODE=oidc + OIDC_* setzen)")
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    r = get_redis()
    await r.set(f"oidc:state:{state}", nonce, ex=_STATE_TTL)
    url = await OidcSvc.get_login_url(cfg, state=state, nonce=nonce)
    return RedirectResponse(url)


@router.get("/callback")
async def callback(request: Request, code: str = "", state: str = ""):
    cfg = oidc_config.load_config()
    if not code or not state:
        raise HTTPException(status_code=400, detail="code/state fehlt")

    r = get_redis()
    nonce = await r.getdel(f"oidc:state:{state}")
    if not nonce:
        raise HTTPException(status_code=400, detail="Ungültiger oder abgelaufener State")

    try:
        tokens = await OidcSvc.exchange_code(cfg, code)
        claims = await OidcSvc.decode_id_token(
            cfg, tokens.get("id_token", ""), nonce=nonce if isinstance(nonce, str) else nonce.decode()
        )
    except OidcError as e:
        raise HTTPException(status_code=401, detail=str(e))

    email = (claims.get("email") or "").lower()
    allowed = get_settings().allowed_email_set
    if not email or (allowed and email not in allowed):
        logger.warning("OIDC-Login abgelehnt für %r (nicht in ALLOWED_EMAILS)", email)
        raise HTTPException(status_code=403, detail="E-Mail nicht freigeschaltet")

    token = create_session_token(email, claims.get("name"))
    resp = RedirectResponse("/")
    resp.set_cookie(
        COOKIE_NAME, token,
        max_age=get_settings().session_max_age,
        httponly=True, samesite="lax", path="/",
    )
    return resp


@router.post("/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


@router.get("/logout")
async def logout_redirect():
    """Abmelden inkl. IdP-Session (RP-initiated Logout).

    Nur das App-Cookie zu löschen reicht nicht: Die SSO-Session beim IdP
    bliebe bestehen und der nächste Login liefe still durch. Daher
    Redirect auf den ``end_session_endpoint`` aus der Discovery (falls
    vorhanden), zurück auf /logged-out. Fail-soft: ohne OIDC/Endpoint
    landet man direkt auf /logged-out."""
    cfg = oidc_config.load_config()
    target = "/logged-out"
    if cfg.is_usable:
        try:
            doc = await _get_discovery(cfg)
            end_session = doc.get("end_session_endpoint")
            if end_session:
                params = {"client_id": cfg.client_id}
                if cfg.redirect_uri:
                    p = urlparse(cfg.redirect_uri)
                    params["post_logout_redirect_uri"] = f"{p.scheme}://{p.netloc}/logged-out"
                target = f"{end_session}?{urlencode(params)}"
        except Exception as e:
            logger.warning("end_session_endpoint nicht ermittelbar: %s", e)
    resp = RedirectResponse(target)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


@router.get("/me")
async def me(user: CurrentUser = Depends(require_user)):
    return {"email": user.email, "name": user.name, "auth_mode": get_settings().auth_mode}
