"""Async Moodle REST client (one instance == one token == one external service)."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from ..errors import (
    FunctionUnavailableError,
    MamcpError,
    MoodleAPIError,
    NetworkRestrictionError,
    PermissionDeniedError,
    ServiceUnavailableError,
    TokenInvalidError,
    UnsafeDestinationError,
    WriteOutcomeUnknownError,
)
from ..redact import register_secret, scrub_text, strip_url_credentials
from .netsafe import check_destination
from .params import flatten

log = logging.getLogger(__name__)

USER_AGENT = "moodle-academic-mcp (community client)"
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024


@dataclass(frozen=True)
class Endpoint:
    """Where a Moodle lives and which network rules apply."""

    base_url: str
    allow_insecure_http: bool = False  # development only
    allow_private_networks: bool = False  # explicit admin opt-in
    extra_hosts: tuple[str, ...] = ()  # e.g. a file CDN that is part of the same site
    timeout: float = 30.0

    @property
    def host(self) -> str:
        return (urlsplit(self.base_url).hostname or "").lower()

    @property
    def allowed_hosts(self) -> set[str]:
        return {self.host, *[h.lower() for h in self.extra_hosts]}

    @property
    def rest_url(self) -> str:
        return self.base_url.rstrip("/") + "/webservice/rest/server.php"

    @property
    def upload_url(self) -> str:
        return self.base_url.rstrip("/") + "/webservice/upload.php"


@dataclass
class CallRecord:
    function: str
    write: bool
    ok: bool
    error: str | None = None


@dataclass
class MoodleClient:
    endpoint: Endpoint
    token: str = field(repr=False)
    service: str = "mobile"
    http: httpx.AsyncClient | None = None
    read_retries: int = 3
    max_concurrency: int = 4
    _own_http: bool = field(default=False, init=False, repr=False)
    _sem: asyncio.Semaphore = field(default=None, init=False, repr=False)  # type: ignore[assignment]
    calls: list[CallRecord] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        register_secret(self.token)
        self._sem = asyncio.Semaphore(self.max_concurrency)
        if self.http is None:
            self.http = httpx.AsyncClient(
                timeout=self.endpoint.timeout,
                follow_redirects=False,
                headers={"User-Agent": USER_AGENT},
                verify=True,
            )
            self._own_http = True

    async def aclose(self) -> None:
        if self._own_http and self.http is not None:
            await self.http.aclose()

    # ------------------------------------------------------------------ REST
    async def call(self, function: str, params: dict[str, Any] | None = None, *, write: bool = False) -> Any:
        """Call a web service function. Reads are retried with bounded backoff; writes never are."""
        await check_destination(
            self.endpoint.rest_url,
            allowed_hosts=self.endpoint.allowed_hosts,
            allow_private=self.endpoint.allow_private_networks,
            allow_insecure_http=self.endpoint.allow_insecure_http,
        )
        form = [("wstoken", self.token), ("wsfunction", function), ("moodlewsrestformat", "json")]
        form += flatten(params)
        attempts = 1 if write else max(1, self.read_retries)
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                async with self._sem:
                    assert self.http is not None
                    resp = await self.http.post(self.endpoint.rest_url, data=dict(form))
                result = self._parse(resp, function)
                self.calls.append(CallRecord(function, write, True))
                return result
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last = e
                if write:
                    self.calls.append(CallRecord(function, True, False, "transport"))
                    raise WriteOutcomeUnknownError(
                        f"Connection problem while calling write function {function}; the server may or may not "
                        "have applied it.",
                        hint="Do NOT retry blindly. Read the current state (e.g. get_submission_status) first.",
                        details={"function": function},
                    ) from None
            except ServiceUnavailableError as e:
                last = e
                if write:
                    raise
            except MamcpError:
                self.calls.append(CallRecord(function, write, False, "api"))
                raise
            if attempt < attempts - 1:
                await asyncio.sleep(min(8.0, 0.5 * (2**attempt)) + random.random() * 0.2)  # noqa: S311
        self.calls.append(CallRecord(function, write, False, "exhausted"))
        if isinstance(last, MamcpError):
            raise last
        raise NetworkRestrictionError(
            f"Could not reach {self.endpoint.host} after {attempts} attempts ({type(last).__name__}).",
            hint="Check your connection. UFSC may require VPN or restrict external access; run `doctor`.",
        )

    def _parse(self, resp: httpx.Response, function: str) -> Any:
        sc = resp.status_code
        if sc in (301, 302, 303, 307, 308):
            loc = resp.headers.get("location", "")
            raise NetworkRestrictionError(
                f"The server redirected the API call to {strip_url_credentials(loc) or 'another URL'} "
                "(usually a login page or an HTTP->HTTPS/SSO redirect).",
                hint="Use the exact https base URL of the Moodle. If access is restricted, connect to the VPN.",
            )
        if sc in (401, 403):
            raise PermissionDeniedError(f"HTTP {sc} from the Moodle server.", function=function)
        if sc == 429:
            raise ServiceUnavailableError(
                "Rate limited (HTTP 429).",
                function=function,
                hint=f"Retry-After: {resp.headers.get('retry-after', 'unknown')}",
            )
        if sc >= 500:
            raise ServiceUnavailableError(f"HTTP {sc} from the Moodle server.", function=function)
        if sc >= 400:
            raise MoodleAPIError(f"HTTP {sc} from the Moodle server.", function=function)
        text = resp.text
        ctype = resp.headers.get("content-type", "")
        try:
            data = resp.json()
        except ValueError:
            stripped = text.lstrip()[:200].lower()
            if "html" in ctype or stripped.startswith("<"):
                raise NetworkRestrictionError(
                    "The server answered with HTML instead of JSON (login page, maintenance page, or a network "
                    "restriction).",
                    hint="Check VPN/network restrictions and that the base URL is right. Run `doctor`.",
                ) from None
            raise MoodleAPIError("Response is not valid JSON.", function=function) from None
        if isinstance(data, dict) and ("exception" in data or "errorcode" in data):
            raise self._map_error(data, function)
        return data

    @staticmethod
    def _map_error(data: dict, function: str) -> MoodleAPIError:
        code = str(data.get("errorcode", ""))
        # Scrub before any branching/echo: a hostile server may put tokens in these fields.
        exc = scrub_text(str(data.get("exception", "")))
        msg = scrub_text(str(data.get("message", "Moodle error")))
        dbg = scrub_text(str(data.get("debuginfo", "")))
        details: dict[str, str] = {}
        if data.get("exception") is not None:
            details["exception"] = exc
        if data.get("debuginfo"):
            details["debuginfo"] = dbg
        kw: dict[str, Any] = {"errorcode": code, "function": function, "details": details}
        if code in {"invalidtoken", "tokenexpired"} or "invalid token" in msg.lower():
            return TokenInvalidError(
                "Token invalid, revoked or expired.",
                hint="Create a new token in /user/managetoken.php and run `configure` again.",
                **kw,
            )
        if code in {"servicenotavailable", "sitemaintenance", "maintenance"}:
            return ServiceUnavailableError(msg, **kw)
        if code == "accessexception" or exc == "webservice_access_exception":
            low = (msg + " " + dbg).lower()
            if "function" in low and (
                "not allowed" in low
                or "not available" in low
                or "add the function" in low
                or "does not exist" in low
                or "unknown" in low
            ):
                return FunctionUnavailableError(
                    f"{function} is not available on this service/token.",
                    **kw,
                    hint="The function is not enabled for the external service of this token (or not installed).",
                )
            return PermissionDeniedError(
                msg or "Access control exception.",
                **kw,
                hint="Either the function is not enabled for this token's external service, or the account lacks "
                "permission. Moodle does not always say which (debug info is usually off).",
            )
        if (
            code in {"nopermissions", "requireloginerror", "cannotviewcourse", "notingroup", "forbidden"}
            or "nopermission" in code
            or "required_capability" in code
        ):
            return PermissionDeniedError(msg, **kw)
        if code in {"invalidrecord", "invalidfunction"} and "external_functions" in (msg + dbg).lower():
            return FunctionUnavailableError(f"{function} does not exist on this Moodle.", **kw)
        if code == "dmlwriteexception" or exc == "dml_write_exception":
            return MoodleAPIError(
                "Moodle failed to write to its own database (dmlwriteexception). Nothing was confirmed saved.",
                **kw,
                hint="This is a server-side error, not a problem with your file, group or permissions. Read the "
                "current state before retrying; if it persists (seen on group assignments), submit through the "
                "Moodle website and tell the course staff.",
            )
        if code == "invalidresponse":
            return MoodleAPIError(
                f"Moodle rejected its own response for {function} (invalidresponse): a server-side data/validation "
                "problem, not your token or parameters.",
                **kw,
                hint="Retrying will not help. For the gradebook, read the rendered table instead with "
                "moodle_call_read('gradereport_user_get_grades_table', {courseid, userid}) (large HTML output), or "
                "check the grades on the Moodle website.",
            )
        return MoodleAPIError(msg or "Moodle error.", **kw)

    # ----------------------------------------------------------------- files
    async def upload_draft(
        self, filename: str, content: bytes, *, itemid: int = 0, filepath: str = "/"
    ) -> dict:
        """Upload one file to the user's draft area through /webservice/upload.php.

        Returns Moodle's description of the draft file (itemid, filename, contextid...).
        Uploading to a draft area is harmless and not a delivery.
        """
        await check_destination(
            self.endpoint.upload_url,
            allowed_hosts=self.endpoint.allowed_hosts,
            allow_private=self.endpoint.allow_private_networks,
            allow_insecure_http=self.endpoint.allow_insecure_http,
        )
        assert self.http is not None
        try:
            async with self._sem:
                resp = await self.http.post(
                    self.endpoint.upload_url,
                    data={"token": self.token, "filepath": filepath, "itemid": str(itemid)},
                    files={"file_1": (filename, content, "application/octet-stream")},
                    timeout=max(self.endpoint.timeout, 120.0),
                )
        except (httpx.TimeoutException, httpx.TransportError):
            raise WriteOutcomeUnknownError(
                "Connection problem during file upload; the draft file may or may not exist.",
                hint="List the draft/submission state before uploading again.",
            ) from None
        data = self._parse(resp, "webservice/upload.php")
        if isinstance(data, list) and data:
            return data[0]
        raise MoodleAPIError("Unexpected response from upload.php.", function="upload.php")

    async def download(self, url: str, *, max_bytes: int = MAX_DOWNLOAD_BYTES) -> tuple[bytes, str]:
        """Download a Moodle file. The token only goes to the profile's Moodle host (redirects re-validated)."""
        assert self.http is not None
        current = self._with_token(url)
        for _ in range(5):
            await check_destination(
                current,
                allowed_hosts=self.endpoint.allowed_hosts,
                allow_private=self.endpoint.allow_private_networks,
                allow_insecure_http=self.endpoint.allow_insecure_http,
            )
            async with self._sem, self.http.stream("GET", current) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    current = urljoin(current, resp.headers.get("location", ""))
                    continue
                if resp.status_code >= 400:
                    raise MoodleAPIError(f"Download failed with HTTP {resp.status_code}.")
                ctype = resp.headers.get("content-type", "application/octet-stream")
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        raise MoodleAPIError("File exceeds the download size limit.")
                if "text/html" in ctype and buf[:200].lstrip().lower().startswith((b"<!doctype", b"<html")):
                    raise NetworkRestrictionError(
                        "Download returned an HTML page instead of the file (login or restriction)."
                    )
                return bytes(buf), ctype
        raise UnsafeDestinationError("Too many redirects while downloading.")

    def _with_token(self, url: str) -> str:
        """Attach the token to a Moodle file URL (webservice/pluginfile.php scheme)."""
        parts = urlsplit(url)
        path = parts.path
        if "/pluginfile.php/" in path and "/webservice/pluginfile.php/" not in path:
            path = path.replace("/pluginfile.php/", "/webservice/pluginfile.php/", 1)
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "token"]
        query.append(("token", self.token))
        return urlunsplit((parts.scheme, parts.netloc, path, urlencode(query), ""))
