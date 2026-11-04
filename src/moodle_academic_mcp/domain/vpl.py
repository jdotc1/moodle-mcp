"""VPL (Virtual Programming Lab) adapter. Uses its own 'vpl' token; never assumed interchangeable with mobile."""

from __future__ import annotations

import base64
from typing import Any

from ..errors import UnsupportedError
from .account import Account
from .files import resolve_files


def _c(acct: Account):  # type: ignore[no-untyped-def]
    if "vpl" not in acct.tokens:
        raise UnsupportedError(
            "No 'vpl' token configured.",
            hint="Create a token for the 'VPL web service' in /user/managetoken.php and run "
            "`configure` with service name 'vpl'.",
        )
    return acct.client("vpl")


def _dec(f: dict) -> dict:
    data = f.get("data", "")
    if f.get("encoding") == 1:
        try:
            data = base64.b64decode(data).decode("utf-8", errors="replace")
        except ValueError:
            data = "<invalid base64>"
    return {"name": f.get("name"), "content": data}


async def info(acct: Account, cmid: int, password: str | None = None) -> dict[str, Any]:
    r = await _c(acct).call("mod_vpl_info", {"id": cmid, "password": password})
    return {
        "cmid": cmid,
        "name": r.get("name"),
        "short_description": r.get("shortdescription"),
        "statement_html": r.get("intro"),
        "requires_password": bool(r.get("reqpassword")),
        "max_files": r.get("maxfiles"),
        "required_files": [_dec(f) for f in r.get("reqfiles", [])],
        "status": "documented_upstream_unvalidated",
    }


async def get_files(acct: Account, cmid: int, password: str | None = None) -> dict[str, Any]:
    r = await _c(acct).call("mod_vpl_open", {"id": cmid, "password": password})
    return {
        "cmid": cmid,
        "files": [_dec(f) for f in r.get("files", [])],
        "comments": r.get("comments"),
        "compilation": r.get("compilation"),
        "evaluation": r.get("evaluation"),
        "grade": r.get("grade"),
    }


async def submit(
    acct: Account, cmid: int, files: list[str], comments: str = "", password: str | None = None
) -> dict[str, Any]:
    """WRITE: saves a new submission in VPL (counts as a submission)."""
    resolved = await resolve_files(acct, files)
    payload = [{"name": f.name, "data": base64.b64encode(f.data).decode(), "encoding": 1} for f in resolved]
    await _c(acct).call(
        "mod_vpl_save", {"id": cmid, "files": payload, "comments": comments, "password": password}, write=True
    )
    after = await get_files(acct, cmid, password)
    sent = {f.name for f in resolved}
    return {
        "saved": sent <= {f["name"] for f in after["files"]},
        "files_now": [f["name"] for f in after["files"]],
    }


async def evaluate(acct: Account, cmid: int, password: str | None = None) -> dict[str, Any]:
    """WRITE (consumes server resources): ask VPL to run the evaluation of your last submission."""
    r = await _c(acct).call("mod_vpl_evaluate", {"id": cmid, "password": password}, write=True)
    return {"started": True, "monitor": r, "next": "Call vpl_result after a few seconds."}


async def result(acct: Account, cmid: int, password: str | None = None) -> dict[str, Any]:
    r = await _c(acct).call("mod_vpl_get_result", {"id": cmid, "password": password}, write=True)
    return {
        "compilation": r.get("compilation"),
        "evaluation": r.get("evaluation"),
        "grade": r.get("grade"),
        "trust": "untrusted_program_output",
    }
