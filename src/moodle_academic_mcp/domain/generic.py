"""Advanced, controlled access to documented web service functions (no raw URL/credential proxy)."""

from __future__ import annotations

import json
from typing import Any

from ..errors import MamcpError, NotConfiguredError, ValidationError
from ..moodle.catalog import Effect
from ..redact import scrub
from .account import Account

MAX_CHARS = 60_000
_USER_PARAMS = {"userid", "userids"}


async def list_functions(
    acct: Account, query: str = "", effect: str | None = None, limit: int = 25, detect: bool = True
) -> dict[str, Any]:
    funcs = acct.catalog.search(query, 200) if query else list(acct.catalog.functions.values())[:200]
    detected: dict[str, set[str] | None] = {}
    if detect:
        for s in acct.services():
            detected[s] = await acct.detected_functions(s)
    out = []
    for f in funcs:
        if effect and f.effect.value != effect:
            continue
        where = [s for s, d in detected.items() if d is not None and f.name in d]
        row = f.to_dict()
        row["state"] = "detected_in_service" if where else "documented" if detect else "documented"
        row["detected_in_services"] = where
        out.append(row)
    return {
        "functions": out[:limit],
        "total_matches": len(out),
        "catalog_sources": acct.catalog.provenance,
        "note": "Documented does not mean authorised for your account. 'detected_in_services' comes from "
        "core_webservice_get_site_info of each token.",
    }


async def call_function(
    acct: Account,
    function: str,
    params: dict[str, Any] | None,
    service: str | None,
    allow_effects: set[Effect],
) -> dict[str, Any]:
    info = acct.catalog.get(function)
    if info is None:
        raise ValidationError(
            f"{function} is not in the bundled catalogs.",
            hint="Use list_functions to see invocable functions.",
        )
    if info.effect not in allow_effects:
        raise ValidationError(
            f"{function} has effect '{info.effect.value}', not allowed through this tool.",
            hint="Use moodle_call_read for read-only functions and moodle_call_write for the rest.",
        )
    if info.sensitive_output:
        raise ValidationError(f"{function} returns secrets and is blocked.")
    problems = acct.catalog.validate(function, params)
    if problems:
        raise ValidationError(f"Parameters rejected for {function}.", details={"problems": problems})
    params = dict(params or {})
    # Identity comes from the authenticated context, never from the model.
    if any(k in params for k in _USER_PARAMS):
        me = await acct.userid()
        for k in _USER_PARAMS & set(params):
            v = params[k]
            ok = (v == me or v == [me] or str(v) == str(me)) if k == "userid" else (v == [me])
            if not ok and not (k == "userid" and v in (0, "0")):
                raise ValidationError(
                    f"{k} must be your own user id ({me}); acting on other users is not allowed."
                )
    if service is None:
        service = "mobile"
        for s in acct.services():
            d = await acct.detected_functions(s)
            if d and function in d:
                service = s
                break
        if function.startswith("mod_attendance_") and "attendance" in acct.tokens:
            service = "attendance"
        if function.startswith("mod_vpl_") and "vpl" in acct.tokens:
            service = "vpl"
    if service not in acct.tokens:
        raise NotConfiguredError(f"No token for service {service!r}.")
    res = await acct.client(service).call(function, params, write=info.effect != Effect.READ)
    res = scrub(res)
    txt = json.dumps(res, ensure_ascii=False)
    truncated = len(txt) > MAX_CHARS
    return {
        "function": function,
        "service": service,
        "effect": info.effect.value,
        "result": res if not truncated else None,
        "truncated_json_prefix": txt[:MAX_CHARS] if truncated else None,
        "provenance": info.source,
    }


def safe_error(e: Exception) -> dict[str, Any]:
    return (
        e.to_dict() if isinstance(e, MamcpError) else {"error": "internal_error", "message": type(e).__name__}
    )
