"""Per-user, per-profile account context. One Account == one student on one institution.

Tokens live only in the MoodleClient instances of this object; there are no module-level singletons
holding "the current user's token".
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx

from ..config import ProfileSettings
from ..errors import MamcpError, NotConfiguredError
from ..moodle.catalog import Catalog, load_catalog
from ..moodle.client import MoodleClient
from ..redact import scrub
from ..runtime import Runtime
from ..store import Store

log = logging.getLogger(__name__)
SENSITIVE_SITEINFO = {"userprivateaccesskey", "userquota", "usermaxuploadfilesize"}


class CredentialSource(Protocol):
    async def tokens(self, user_key: str, profile: ProfileSettings) -> dict[str, str]: ...


@dataclass
class Account:
    user_key: str
    profile: ProfileSettings
    tokens: dict[str, str]
    store: Store
    http: httpx.AsyncClient | None = None
    catalog: Catalog = field(default_factory=load_catalog)
    runtime: Runtime = field(default_factory=Runtime)
    _clients: dict[str, MoodleClient] = field(default_factory=dict, init=False, repr=False)
    _siteinfo: dict[str, dict] = field(default_factory=dict, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    # ---------------------------------------------------------------- basics
    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.profile.timezone)

    @property
    def base_url(self) -> str:
        return self.profile.base_url

    def services(self) -> list[str]:
        return sorted(self.tokens)

    def client(self, service: str = "mobile") -> MoodleClient:
        if service not in self.tokens:
            raise NotConfiguredError(
                f"No token configured for service {service!r} on profile {self.profile.name!r}.",
                hint=f"Configured services: {self.services() or 'none'}. Create a token at "
                f"{self.base_url}/user/managetoken.php and run `moodle-academic-mcp configure`.",
            )
        if service not in self._clients:
            self._clients[service] = MoodleClient(
                self.profile.endpoint(), self.tokens[service], service, http=self.http
            )
        return self._clients[service]

    async def aclose(self) -> None:
        for c in self._clients.values():
            await c.aclose()

    def link(self, path: str) -> str:
        return self.base_url.rstrip("/") + "/" + path.lstrip("/")

    # ---------------------------------------------------------------- identity
    async def site_info(self, service: str = "mobile", *, refresh: bool = False) -> dict:
        """core_webservice_get_site_info, with sensitive keys removed (userprivateaccesskey etc.)."""
        if service in self._siteinfo and not refresh:
            return self._siteinfo[service]
        info = await self.client(service).call("core_webservice_get_site_info")
        clean = {k: v for k, v in scrub(info).items() if k not in SENSITIVE_SITEINFO}
        self._siteinfo[service] = clean
        return clean

    async def userid(self) -> int:
        return int((await self.site_info("mobile"))["userid"])

    async def detected_functions(self, service: str = "mobile") -> set[str] | None:
        try:
            info = await self.site_info(service)
        except MamcpError:
            return None
        return {f["name"] for f in info.get("functions", [])}

    async def service_identity(self, service: str) -> dict[str, Any]:
        """Try to prove that `service`'s token belongs to the same Moodle user as the mobile token.

        Only verifiable evidence counts: equal userid from core_webservice_get_site_info on both tokens.
        Otherwise the state stays 'unknown' and records are never mixed between profiles/services.
        """
        if service == "mobile":
            return {
                "service": service,
                "verified": True,
                "evidence": "primary service",
                "userid": await self.userid(),
            }
        try:
            mine = await self.userid()
            other = int((await self.site_info(service))["userid"])
        except MamcpError as e:
            return {
                "service": service,
                "verified": False,
                "userid": None,
                "evidence": f"cannot prove identity: core_webservice_get_site_info unavailable on this service "
                f"({e.code})",
            }
        ok = mine == other
        return {
            "service": service,
            "verified": ok,
            "userid": other,
            "evidence": "same userid from core_webservice_get_site_info" if ok else "userid differs",
        }

    async def require_verified_identity(self, service: str) -> dict[str, Any]:
        ident = await self.service_identity(service)
        await self.store.set_identity(
            self.user_key,
            self.profile.name,
            service,
            ident.get("userid"),
            ident["verified"],
            ident["evidence"],
        )
        return ident


def paginate(items: list[Any], offset: int = 0, limit: int = 50) -> dict[str, Any]:
    """Uniform pagination envelope: never hides remaining items silently."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    page = items[offset : offset + limit]
    nxt = offset + limit if offset + limit < len(items) else None
    return {
        "items": page,
        "total": len(items),
        "offset": offset,
        "limit": limit,
        "next_offset": nxt,
        "has_more": nxt is not None,
    }
