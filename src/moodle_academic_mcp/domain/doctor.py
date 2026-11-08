"""Connection diagnostics shared by the `doctor` CLI, onboarding and the moodle_status tool."""

from __future__ import annotations

from typing import Any

import httpx

from ..config import ProfileSettings
from ..errors import MamcpError
from ..moodle.client import MoodleClient
from ..moodle.netsafe import check_destination
from ..redact import scrub
from .account import Account
from .capabilities import matrix


async def probe_endpoint(profile: ProfileSettings, http: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Reachability without credentials: an invalid token must produce a JSON 'invalidtoken' error."""
    ep = profile.endpoint()
    res: dict[str, Any] = {"base_url": profile.base_url, "host": ep.host}
    try:
        await check_destination(
            ep.rest_url,
            allowed_hosts=ep.allowed_hosts,
            allow_private=ep.allow_private_networks,
            allow_insecure_http=ep.allow_insecure_http,
        )
    except MamcpError as e:
        return {**res, "ok": False, "stage": "destination_policy", **scrub(e.to_dict())}
    probe = MoodleClient(ep, "invalid-probe-token", "probe", http=http, read_retries=1)
    try:
        await probe.call("core_webservice_get_site_info")
        res.update(ok=True, stage="rest", note="Endpoint answered (unexpectedly accepted a dummy token?)")
    except MamcpError as e:
        if e.code in ("token_invalid", "permission_denied"):
            res.update(ok=True, stage="rest", note="REST endpoint reachable and web services enabled.")
        elif e.code == "network_restriction":
            res.update(ok=False, stage="network", **scrub(e.to_dict()))
        else:
            res.update(ok=False, stage="rest", **scrub(e.to_dict()))
    finally:
        await probe.aclose()
    return res


async def diagnose(acct: Account, detect: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {"profile": acct.profile.to_public(), "services": {}}
    out["endpoint"] = await probe_endpoint(acct.profile, acct.http)
    for s in acct.services():
        entry: dict[str, Any] = {"token_configured": True}
        try:
            info = await acct.site_info(s)
            entry.update(
                ok=True,
                user_id=info.get("userid"),
                username=info.get("username"),
                full_name=info.get("fullname"),
                site_name=info.get("sitename"),
                moodle_release=info.get("release"),
                moodle_version=info.get("version"),
                functions_in_service=len(info.get("functions", [])),
                identity=(await acct.service_identity(s)) if s != "mobile" else {"verified": True},
            )
        except MamcpError as e:
            entry.update(ok=False, **scrub(e.to_dict()))
            if e.code == "function_unavailable" or e.code == "permission_denied":
                entry["note"] = (
                    "This token may be valid but its service lacks core_webservice_get_site_info "
                    "(normal for e.g. Attendance/VPL services). Identity stays unknown."
                )
        out["services"][s] = entry
    if not acct.services():
        out["services"] = {}
        out["hint"] = "No tokens configured."
    if detect:
        m = await matrix(acct)
        out["features"] = {k: v["states"] for k, v in m["features"].items()}
    return out
