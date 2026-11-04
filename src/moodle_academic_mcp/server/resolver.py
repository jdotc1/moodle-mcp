"""Who is calling, and which Moodle credentials belong to them. Identity comes from the authenticated
context only; model-supplied user ids, tenants or profiles are never trusted for authorisation."""

from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass

import httpx

from ..config import AppConfig, LocalTokenStore, ProfileSettings
from ..domain.account import Account
from ..errors import AuthError, NotConfiguredError
from ..runtime import Runtime
from ..store import Store
from ..vault import Vault

log = logging.getLogger(__name__)
LOCAL_USER = "local"

# Set by HTTP layers (FastAPI dependency / middleware) for the duration of one request.
_current_user: contextvars.ContextVar[str | None] = contextvars.ContextVar("mamcp_user", default=None)


def set_current_user(user_key: str | None) -> contextvars.Token:
    return _current_user.set(user_key)


def reset_current_user(token: contextvars.Token) -> None:
    _current_user.reset(token)


def current_user_key(hosted: bool) -> str:
    uk = _current_user.get()
    if uk:
        return uk
    try:  # MCP SDK puts the verified bearer token of the *current request* in a contextvar
        from mcp.server.auth.middleware.auth_context import get_access_token

        at = get_access_token()
        if at is not None:
            return at.client_id
    except Exception:  # pragma: no cover
        pass
    if hosted:
        raise AuthError("Unauthenticated request.", hint="Send a valid bearer token.")
    return LOCAL_USER


class LocalCredentialSource:
    def __init__(self, tokens: LocalTokenStore):
        self.tokens_store = tokens

    async def tokens(self, user_key: str, profile: ProfileSettings) -> dict[str, str]:
        return {
            s: t
            for s in self.tokens_store.services(profile.name)
            if (t := self.tokens_store.get(profile.name, s))
        }


class DbCredentialSource:
    def __init__(self, store: Store, vault: Vault):
        self.store, self.vault = store, vault

    async def tokens(self, user_key: str, profile: ProfileSettings) -> dict[str, str]:
        out: dict[str, str] = {}
        for row in await self.store.list_credentials(user_key, profile.name):
            ct = await self.store.get_credential(user_key, profile.name, row["service"])
            if ct and (tok := self.vault.open(user_key, profile.name, row["service"], ct)):
                out[row["service"]] = tok
        return out

    async def put(self, user_key: str, profile: str, service: str, token: str) -> None:
        await self.store.ensure_user(user_key)
        await self.store.put_credential(
            user_key, profile, service, self.vault.seal(user_key, profile, service, token)
        )

    async def delete(self, user_key: str, profile: str, service: str) -> None:
        await self.store.delete_credential(user_key, profile, service)


@dataclass
class Resolver:
    config: AppConfig
    store: Store
    runtime: Runtime
    creds: LocalCredentialSource | DbCredentialSource
    http: httpx.AsyncClient | None = (
        None  # injectable for tests (ASGI transport); None -> one client per account
    )
    hosted: bool = False

    def __post_init__(self) -> None:
        self._cache: dict[tuple[str, str], Account] = {}

    async def account(self, profile_name: str | None = None) -> Account:
        user_key = current_user_key(self.hosted)
        profile = self.config.profile(profile_name)
        tokens = await self.creds.tokens(user_key, profile)
        if "mobile" not in tokens and not tokens:
            raise NotConfiguredError(
                f"No Moodle token stored for profile {profile.name!r}.",
                hint=(
                    "Open the onboarding page of this server and add your token."
                    if self.hosted
                    else "Run: moodle-academic-mcp configure"
                ),
            )
        key = (user_key, profile.name)
        cached = self._cache.get(key)
        if cached is not None and cached.tokens == tokens:
            return cached
        if cached is not None:
            await cached.aclose()
        acct = Account(
            user_key=user_key,
            profile=profile,
            tokens=tokens,
            store=self.store,
            http=self.http,
            runtime=self.runtime,
        )
        self._cache[key] = acct
        return acct

    async def forget(self, user_key: str, profile: str | None = None) -> None:
        for k in [k for k in self._cache if k[0] == user_key and (profile is None or k[1] == profile)]:
            await self._cache.pop(k).aclose()

    async def aclose(self) -> None:
        for a in self._cache.values():
            await a.aclose()
        self._cache.clear()
