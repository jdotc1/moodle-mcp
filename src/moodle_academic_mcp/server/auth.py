"""Authentication for the hosted MCP/HTTP server.

Two verifiers, same interface:
  OIDCVerifier      production: validates JWT access tokens from an existing OAuth2/OIDC provider (JWKS).
  DevTokenVerifier  development: opaque bearer tokens created with `moodle-academic-mcp dev-token <user>`.

The bearer token authenticates the user to *this* server only. Moodle tokens are separate, stored encrypted,
and never used as bearer tokens or returned to the model.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass

import jwt
from mcp.server.auth.provider import AccessToken

from ..errors import ConfigError
from ..store import Store

log = logging.getLogger(__name__)


@dataclass
class DevTokenVerifier:
    store: Store
    resource: str | None = None

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token.startswith("mamcp_dev_"):
            return None
        row = await self.store.lookup_dev_token(hashlib.sha256(token.encode()).hexdigest())
        if not row:
            return None
        return AccessToken(
            token="***",
            client_id=row["user_key"],
            scopes=["moodle"],
            expires_at=row["expires_at"],
            resource=self.resource,
            subject=row["user_key"],
        )


class OIDCVerifier:
    def __init__(
        self,
        issuer: str,
        audience: str,
        jwks_url: str | None = None,
        *,
        algorithms: list[str] | None = None,
        required_scope: str | None = None,
        allow_insecure: bool = False,
        leeway: int = 30,
    ):
        if not issuer.startswith("https://") and not allow_insecure:
            raise ConfigError("OIDC issuer must be https.")
        self.issuer = issuer.rstrip("/")
        self.audience = audience
        self.jwks_url = jwks_url or f"{self.issuer}/.well-known/jwks.json"
        self.algorithms = algorithms or ["RS256", "ES256"]
        self.required_scope = required_scope
        self.leeway = leeway
        self._jwks = jwt.PyJWKClient(self.jwks_url, cache_keys=True, lifespan=3600, timeout=10)

    def _decode(self, token: str) -> dict:
        key = self._jwks.get_signing_key_from_jwt(token).key
        return jwt.decode(
            token,
            key,
            algorithms=self.algorithms,
            audience=self.audience,
            issuer=self.issuer,
            leeway=self.leeway,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        if token.count(".") != 2:
            return None
        try:
            claims = await asyncio.to_thread(self._decode, token)
        except Exception as e:  # signature, expiry, audience, JWKS fetch... all mean "not authenticated"
            log.info("rejected bearer token: %s", type(e).__name__)
            return None
        scopes = (
            claims.get("scope", "").split() if isinstance(claims.get("scope"), str) else claims.get("scp", [])
        )
        if self.required_scope and self.required_scope not in scopes:
            return None
        user_key = f"{claims['iss']}|{claims['sub']}"
        return AccessToken(
            token="***",
            client_id=user_key,
            scopes=list(scopes),
            expires_at=int(claims["exp"]),
            resource=self.audience,
            subject=claims["sub"],
            claims={"iss": claims["iss"]},
        )


def now() -> int:
    return int(time.time())
