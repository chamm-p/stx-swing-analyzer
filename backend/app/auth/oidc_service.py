"""Generic-OIDC-Flow via Authlib (V6).

Ersetzt die V5-Implementierung auf Basis von ``python-keycloak``. Statt
einer Keycloak-spezifischen Library nutzen wir den OIDC-Discovery-
Mechanismus (``.well-known/openid-configuration``) + JWKS-basierte
ID-Token-Verifikation. Damit ist jeder OIDC-konforme IdP anbindbar
(Keycloak, Microsoft Entra ID, Authentik, …) ohne Provider-spezifischen
Code.

Sicherheit:
- ID-Token wird gegen die JWKS des Providers signatur-verifiziert
  (vorher: ``get_unverified_claims`` Fallback — unsicher).
- ``iss`` / ``aud`` / ``exp`` werden geprüft, ``nonce`` gegen den vom
  Backend ausgestellten State-Token gebunden (Replay-Schutz).

Caching: Discovery-Dokument + JWKS werden pro Provider-URL mit kurzem
TTL gecacht, damit nicht jeder Login zwei HTTP-Roundtrips kostet.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from authlib.jose import JsonWebKey, jwt as jose_jwt
from authlib.jose.errors import JoseError

from app.auth.oidc_config import OidcProviderConfig

logger = logging.getLogger(__name__)

# (well_known_url) → (expires_at, document)
_DISCOVERY_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
# (jwks_uri) → (expires_at, JsonWebKeySet)
_JWKS_CACHE: dict[str, tuple[float, Any]] = {}
_DISCOVERY_TTL = 3600.0   # 1h — Endpunkte ändern sich quasi nie
_JWKS_TTL = 3600.0        # 1h — Key-Rotation ist selten; bei kid-Miss refetch


class OidcError(RuntimeError):
    """OIDC-Flow-Fehler (Discovery, Token-Exchange, Verifikation)."""


async def _get_discovery(cfg: OidcProviderConfig) -> dict[str, Any]:
    """Lädt (gecacht) das OIDC-Discovery-Dokument."""
    url = cfg.well_known_url
    now = time.time()
    cached = _DISCOVERY_CACHE.get(url)
    if cached and cached[0] > now:
        return cached[1]

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        doc = resp.json()

    for required in ("authorization_endpoint", "token_endpoint", "jwks_uri", "issuer"):
        if required not in doc:
            raise OidcError(f"Discovery-Dokument fehlt '{required}' ({url})")

    _DISCOVERY_CACHE[url] = (now + _DISCOVERY_TTL, doc)
    return doc


async def _get_jwks(jwks_uri: str, *, force: bool = False):
    """Lädt (gecacht) die JWKS und parst sie zu einem JsonWebKeySet."""
    now = time.time()
    cached = _JWKS_CACHE.get(jwks_uri)
    if cached and cached[0] > now and not force:
        return cached[1]

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(jwks_uri)
        resp.raise_for_status()
        jwks_data = resp.json()

    key_set = JsonWebKey.import_key_set(jwks_data)
    _JWKS_CACHE[jwks_uri] = (now + _JWKS_TTL, key_set)
    return key_set


class OidcSvc:
    """Stateless OIDC-Helper. Alle Methoden bekommen die aufgelöste
    Provider-Config übergeben (keine Modul-globale Keycloak-Instanz mehr)."""

    @classmethod
    async def get_login_url(cls, cfg: OidcProviderConfig, *, state: str, nonce: str) -> str:
        """Baut die Authorization-Redirect-URL aus dem Discovery-Endpunkt."""
        if not cfg.is_usable:
            raise OidcError("OIDC ist nicht konfiguriert")
        doc = await _get_discovery(cfg)
        params = {
            "client_id": cfg.client_id,
            "response_type": "code",
            "redirect_uri": cfg.redirect_uri,
            "scope": cfg.scopes,
            "state": state,
            "nonce": nonce,
        }
        return f"{doc['authorization_endpoint']}?{urlencode(params)}"

    @classmethod
    async def exchange_code(cls, cfg: OidcProviderConfig, code: str) -> dict[str, Any]:
        """Tauscht den Authorization-Code gegen Tokens (token_endpoint)."""
        if not cfg.is_usable:
            raise OidcError("OIDC ist nicht konfiguriert")
        doc = await _get_discovery(cfg)
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": cfg.redirect_uri,
            "client_id": cfg.client_id,
        }
        if cfg.client_secret:
            data["client_secret"] = cfg.client_secret

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                doc["token_endpoint"],
                data=data,
                headers={"Accept": "application/json"},
            )
        if resp.status_code >= 400:
            logger.error("OIDC token exchange fehlgeschlagen (%s): %s",
                         resp.status_code, resp.text[:500])
            raise OidcError(f"Token-Exchange fehlgeschlagen ({resp.status_code})")
        return resp.json()

    @classmethod
    async def decode_id_token(
        cls, cfg: OidcProviderConfig, id_token: str, *, nonce: str | None = None,
    ) -> dict[str, Any]:
        """Verifiziert + decodiert das ID-Token gegen die Provider-JWKS.

        Prüft Signatur, ``iss`` (== Discovery-issuer), ``aud`` (== client_id),
        ``exp`` und — falls übergeben — ``nonce``."""
        doc = await _get_discovery(cfg)
        jwks_uri = doc["jwks_uri"]

        claims_options = {
            "iss": {"essential": True, "value": doc["issuer"]},
            "aud": {"essential": True, "value": cfg.client_id},
            "exp": {"essential": True},
        }
        if nonce is not None:
            claims_options["nonce"] = {"essential": True, "value": nonce}

        async def _verify(force_jwks: bool):
            key_set = await _get_jwks(jwks_uri, force=force_jwks)
            claims = jose_jwt.decode(id_token, key_set, claims_options=claims_options)
            claims.validate()  # exp/iss/aud/nonce
            return dict(claims)

        try:
            return await _verify(force_jwks=False)
        except JoseError as e:
            # kid evtl. rotiert → JWKS einmal frisch ziehen und erneut.
            logger.info("ID-Token-Verifikation 1. Versuch fehlgeschlagen (%s) — JWKS refetch", e)
            try:
                return await _verify(force_jwks=True)
            except JoseError as e2:
                logger.error("ID-Token-Verifikation endgültig fehlgeschlagen: %s", e2)
                raise OidcError("ID-Token-Verifikation fehlgeschlagen") from e2

    @staticmethod
    def extract_groups(cfg: OidcProviderConfig, claims: dict[str, Any]) -> list[str]:
        """Sammelt Gruppen-/Rollen-Strings aus dem ID-Token.

        Deckt drei gängige Setups ab, dedupliziert zu einer Liste:
        1. Konfigurierter ``groups_claim`` — unterstützt Dot-Pfade für
           verschachtelte Claims (z. B. ``realm_access.roles``). Keycloak
           emittiert bei aktivem Group-Mapper Gruppennamen, Entra Object-IDs.
        2. Keycloak-Realm-Rollen (``realm_access.roles``).
        3. Keycloak-Client-Rollen (``resource_access.<client>.roles``).

        So funktioniert das Mapping auch bei role-only-Setups OHNE Group-
        Mapper — der Admin trägt die tatsächlich ankommenden Strings (im
        Nutzer-Dialog sichtbar) unter „OIDC-Gruppennamen" ein."""

        def as_list(raw: Any) -> list[str]:
            if raw is None:
                return []
            if isinstance(raw, str):
                return [raw]
            if isinstance(raw, (list, tuple)):
                return [str(g) for g in raw if g]
            return []

        def dig(path: str) -> Any:
            cur: Any = claims
            for part in path.split("."):
                if isinstance(cur, dict):
                    cur = cur.get(part)
                else:
                    return None
            return cur

        out: list[str] = []
        seen: set[str] = set()

        def add(vals: list[str]) -> None:
            for v in vals:
                if v not in seen:
                    seen.add(v)
                    out.append(v)

        # 1) konfigurierter Claim (Dot-Path-fähig)
        add(as_list(dig(cfg.groups_claim)))
        # 2) Keycloak-Realm-Rollen
        ra = claims.get("realm_access")
        if isinstance(ra, dict):
            add(as_list(ra.get("roles")))
        # 3) Keycloak-Client-Rollen (alle Clients)
        resacc = claims.get("resource_access")
        if isinstance(resacc, dict):
            for client_obj in resacc.values():
                if isinstance(client_obj, dict):
                    add(as_list(client_obj.get("roles")))
        return out

    @staticmethod
    def map_role(cfg: OidcProviderConfig, groups: list[str]) -> str | None:
        """Bestimmt die interne Rolle aus den IdP-Gruppen.

        Liefert die höchstprivilegierte gematchte Rolle (admin > user >
        kiosk) oder ``None``, wenn kein Mapping konfiguriert ist bzw. keine
        Gruppe matcht. ``None`` heißt für den Aufrufer: Rolle NICHT aus dem
        IdP ableiten (Bestand/Default behalten)."""
        if not cfg.group_role_mapping or not groups:
            return None
        precedence = {"admin": 3, "user": 2, "kiosk": 1}
        best: str | None = None
        best_rank = 0
        group_set = set(groups)
        for grp, role in cfg.group_role_mapping.items():
            if grp in group_set:
                rank = precedence.get(role, 0)
                if rank > best_rank:
                    best_rank = rank
                    best = role
        return best
