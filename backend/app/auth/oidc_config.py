"""OIDC-Provider-Config — vereinfachte Env-Variante der cura_llm-Config.

Single-User-System: keine DB-gepflegte Provider-Config, kein
Group→Role-Mapping. Die Felder ``groups_claim``/``group_role_mapping``
bleiben erhalten, damit ``oidc_service.py`` unverändert aus cura_llm
übernommen werden konnte.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import get_settings

DEFAULT_SCOPES = "openid email profile"


@dataclass
class OidcProviderConfig:
    enabled: bool = False
    discovery_url: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    redirect_uri: str | None = None
    scopes: str = DEFAULT_SCOPES
    groups_claim: str = "groups"
    group_role_mapping: dict[str, str] = field(default_factory=dict)
    provider_label: str = "SSO"

    @property
    def is_usable(self) -> bool:
        return bool(self.enabled and self.discovery_url and self.client_id and self.redirect_uri)

    @property
    def well_known_url(self) -> str:
        url = (self.discovery_url or "").rstrip("/")
        suffix = "/.well-known/openid-configuration"
        return url if url.endswith(suffix) else url + suffix


def load_config() -> OidcProviderConfig:
    s = get_settings()
    return OidcProviderConfig(
        enabled=s.auth_mode == "oidc" and bool(s.oidc_discovery_url),
        discovery_url=s.oidc_discovery_url,
        client_id=s.oidc_client_id,
        client_secret=s.oidc_client_secret,
        redirect_uri=s.oidc_redirect_uri,
    )
