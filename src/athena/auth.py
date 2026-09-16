"""Static API tokens and optional OAuth resource-server verification for MCP."""
from __future__ import annotations

import hmac
import os
from functools import lru_cache
from urllib.parse import urlparse

import jwt


def oauth_settings() -> dict[str, str] | None:
    keys = ("ATHENA_PUBLIC_BASE_URL", "ATHENA_OAUTH_ISSUER", "ATHENA_OAUTH_AUDIENCE", "ATHENA_OAUTH_JWKS_URL")
    values = {key: os.getenv(key, "").strip().rstrip("/") for key in keys}
    if not all(values.values()): return None
    if any(urlparse(values[key]).scheme != "https" for key in ("ATHENA_PUBLIC_BASE_URL", "ATHENA_OAUTH_ISSUER", "ATHENA_OAUTH_JWKS_URL")):
        return None
    if values["ATHENA_OAUTH_AUDIENCE"] != values["ATHENA_PUBLIC_BASE_URL"] + "/mcp":
        return None
    values["ATHENA_OAUTH_SCOPE"] = os.getenv("ATHENA_OAUTH_SCOPE", "athena:access").strip() or "athena:access"
    return values


def resource_metadata() -> dict | None:
    settings = oauth_settings()
    if not settings: return None
    return {"resource": settings["ATHENA_PUBLIC_BASE_URL"] + "/mcp", "authorization_servers": [settings["ATHENA_OAUTH_ISSUER"]], "scopes_supported": [settings["ATHENA_OAUTH_SCOPE"]]}


def auth_challenge() -> str:
    settings = oauth_settings()
    if not settings: return "Bearer"
    return f'Bearer resource_metadata="{settings["ATHENA_PUBLIC_BASE_URL"]}/.well-known/oauth-protected-resource", scope="{settings["ATHENA_OAUTH_SCOPE"]}", error="invalid_token", error_description="Authenticate to ATHENA"'


@lru_cache(maxsize=4)
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_jwk_set=True, lifespan=300)


def verify_bearer(header: str | None) -> bool:
    if not header or not header.startswith("Bearer "): return False
    supplied = header[7:]
    static_tokens = [value for name in ("ATHENA_API_TOKEN", "ATHENA_API_TOKEN_PREVIOUS") if (value := os.getenv(name, "").strip())]
    if any(hmac.compare_digest(supplied, token) for token in static_tokens): return True
    settings = oauth_settings()
    if not settings or supplied.count(".") != 2 or len(supplied) > 8192: return False
    try:
        key = _jwks_client(settings["ATHENA_OAUTH_JWKS_URL"]).get_signing_key_from_jwt(supplied)
        claims = jwt.decode(supplied, key.key, algorithms=["RS256", "ES256"], issuer=settings["ATHENA_OAUTH_ISSUER"], audience=settings["ATHENA_OAUTH_AUDIENCE"], options={"require": ["iss", "aud", "exp"]})
        scope = claims.get("scope", "")
        scopes = set(scope.split()) if isinstance(scope, str) else set(claims.get("scp", ()))
        return settings["ATHENA_OAUTH_SCOPE"] in scopes
    except (jwt.PyJWTError, ValueError, OSError):
        return False
