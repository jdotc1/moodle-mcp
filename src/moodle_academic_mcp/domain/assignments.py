"""Assignments: statements, status (individual/group), and the real delivery flow."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from typing import Any

from ..errors import (
    MamcpError,
    MoodleAPIError,
    NotFoundError,
    ValidationError,
    WriteOutcomeUnknownError,
)
from ..redact import strip_url_credentials
from ..timeutil import iso
from .account import Account, paginate
from .courses import course_ids
from .files import ResolvedFile, resolve_files


def _cfg(a: dict) -> dict[str, Any]:
    """Flatten the plugin config list into {'file.enabled': '1', ...}."""
    out = {}
    for c in a.get("configs", []):
        out[f"{c.get('subtype', '')}_{c.get('plugin')}.{c.get('name')}"] = c.get("value")
    return out


def _view(acct: Account, a: dict, course: dict | None = None, detail: bool = False) -> dict[str, Any]:
    cfg = _cfg(a)
    tz = acct.tz
    v: dict[str, Any] = {
        "assignment_id": a["id"],
        "cmid": a.get("cmid"),
        "course_id": a.get("course"),
        "course": (course or {}).get("shortname"),
        "name": a["name"],
        "opens_at": iso(a.get("allowsubmissionsfromdate"), tz),
        "due_at": iso(a.get("duedate"), tz),
        "cutoff_at": iso(a.get("cutoffdate"), tz),  # hard close: differs from the due date
        "grading_due_at": iso(a.get("gradingduedate"), tz),
        "time_limit_seconds": a.get("timelimit") or None,
        "team_submission": bool(a.get("teamsubmission")),
        "requires_draft_submit": bool(a.get("submissiondrafts")),
        "requires_statement": bool(a.get("requiresubmissionstatement")),
        "max_attempts": a.get("maxattempts"),
        "accepts_online_text": cfg.get("assignsubmission_onlinetext.enabled") == "1",
        "accepts_files": cfg.get("assignsubmission_file.enabled") == "1",
        "max_files": int(cfg.get("assignsubmission_file.maxfilesubmissions") or 0) or None,
        "max_file_bytes": int(cfg.get("assignsubmission_file.maxsubmissionsizebytes") or 0) or None,
        "file_types": [
            t for t in re.split(r"[;,\s]+", cfg.get("assignsubmission_file.filetypeslist") or "") if t
        ],
        "link": acct.link(f"mod/assign/view.php?id={a.get('cmid')}") if a.get("cmid") else None,
        "date_notes": [],
    }
    if a.get("duedate") and a.get("cutoffdate") and a["cutoffdate"] != a["duedate"]:
        v["date_notes"].append(
            "due_at is the deadline; submissions are still accepted (marked late) until cutoff_at."
        )
    if a.get("duedate") and not a.get("cutoffdate"):
        v["date_notes"].append("No hard cutoff configured: late submissions are accepted.")
    v["date_notes"].append(
        "Individual extensions and group overrides are only visible in the submission status."
    )
    if detail:
        v["statement_html"] = a.get("intro")
        v["attachments"] = [
            {
                "filename": f.get("filename"),
                "size": f.get("filesize"),
                "mimetype": f.get("mimetype"),
                "url": strip_url_credentials(f.get("fileurl", "")),
            }
            for f in (a.get("introattachments") or [])
        ]
    return v


async def _fetch_assignments(acct: Account, ids: list[int]) -> list[tuple[dict, dict]]:
    raw = await acct.client().call("mod_assign_get_assignments", {"courseids": ids})
    out = []
    for c in raw.get("courses", []):
        for a in c.get("assignments", []):
            out.append((c, a))
    return out


async def list_assignments(
    acct: Account, course_id: int | None = None, offset: int = 0, limit: int = 50
) -> dict[str, Any]:
    ids = [course_id] if course_id else await course_ids(acct)
    pairs = await _fetch_assignments(acct, ids) if ids else []
    items = sorted(
        (_view(acct, a, c) for c, a in pairs), key=lambda x: (x["due_at"] is None, x["due_at"] or "")
    )
    return paginate(items, offset, limit)


async def get_assignment(acct: Account, assignment_id: int, course_id: int | None = None) -> dict[str, Any]:
    ids = [course_id] if course_id else await course_ids(acct, only_current=False)
    for c, a in await _fetch_assignments(acct, ids):
        if a["id"] == assignment_id:
            return _view(acct, a, c, detail=True)
    raise NotFoundError(f"Assignment {assignment_id} not found among your courses.")


def derive_state(status: dict, assignment: dict | None) -> dict[str, Any]:
    """Normalise Moodle's submission status into one explicit state."""
    la = status.get("lastattempt") or {}
    sub = la.get("submission") or {}
    team = la.get("teamsubmission") or {}
    effective = team if (la.get("teamsubmissionenabled") or team) and team else sub
    raw = (effective or {}).get("status")  # new | draft | submitted | reopened
    fb = status.get("feedback") or {}
    grading = la.get("gradingstatus")
    graded = grading == "graded" or bool(fb.get("grade"))
    if graded:
        state = "graded"
    elif raw == "submitted":
        state = "submitted"
    elif raw == "draft":
        state = "draft"
    elif raw == "reopened":
        state = "reopened"
    else:
        state = "not_submitted"
    return {
        "state": state,
        "raw_status": raw,
        "grading_status": grading,
        "graded": graded,
        "locked": bool(la.get("locked")),
        "can_edit": bool(la.get("canedit")),
        "can_submit": bool(la.get("cansubmit")),
        "is_group": bool(la.get("teamsubmissionenabled")),
        "group_id": la.get("submissiongroup") or None,
    }


def _plugin_files(submission: dict, acct: Account) -> tuple[list[dict], str | None]:
    files, text = [], None
    for p in submission.get("plugins", []) or []:
        for fa in p.get("fileareas", []) or []:
            for f in fa.get("files", []) or []:
                files.append(
                    {
                        "filename": f.get("filename"),
                        "size": f.get("filesize"),
                        "modified": iso(f.get("timemodified"), acct.tz),
                        "url": strip_url_credentials(f.get("fileurl", "")),
                    }
                )
        for ef in p.get("editorfields", []) or []:
            if p.get("type") == "onlinetext":
                text = ef.get("text")
    return files, text


async def submission_status(acct: Account, assignment_id: int) -> dict[str, Any]:
    uid = await acct.userid()
    st = await acct.client().call(
        "mod_assign_get_submission_status", {"assignid": assignment_id, "userid": uid}
    )
    la = st.get("lastattempt") or {}
    mine = la.get("submission") or {}
    team = la.get("teamsubmission") or {}
    derived = derive_state(st, None)
    chosen = team if (derived["is_group"] and team) else mine
    files, text = _plugin_files(chosen, acct)
    fb = st.get("feedback") or {}
    ext = la.get("extensionduedate")
    out = {
        "assignment_id": assignment_id,
        **derived,
        "submission_id": chosen.get("id"),
        "attempt": chosen.get("attemptnumber"),
        "last_modified": iso(chosen.get("timemodified"), acct.tz),
        "files": files,
        "online_text_present": bool(text),
        "online_text": text,
        "extension_due_at": iso(ext, acct.tz) if ext else None,
        "grade": {
            "display": fb.get("gradefordisplay"),
            "graded_at": iso((fb.get("grade") or {}).get("timemodified"), acct.tz),
        }
        if fb
        else None,
        "teacher_feedback_plugins": [
            {"type": p.get("type"), "text": [e.get("text") for e in p.get("editorfields", [])]}
            for p in fb.get("plugins", []) or []
        ],
        "group_members_note": "Group submissions count for the whole group." if derived["is_group"] else None,
        "warnings": [w.get("message") for w in st.get("warnings", [])],
    }
    if derived["is_group"] and not chosen:
        out["warnings"].append("Group assignment but no group submission yet (are you in a group?).")
    return out


async def statuses_for(acct: Account, assignment_ids: list[int]) -> dict[int, dict]:
    sem = asyncio.Semaphore(4)

    async def one(i: int) -> tuple[int, dict]:
        async with sem:
            try:
                return i, await submission_status(acct, i)
            except MamcpError as e:
                return i, {"state": "unknown", "error": e.to_dict()}

    return dict(await asyncio.gather(*[one(i) for i in assignment_ids]))


# ============================================================================== writes
def _sha(*parts: bytes | str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else p.encode())
        h.update(b"\x00")
    return h.hexdigest()


def _check_constraints(view: dict, files: list[ResolvedFile], text: str | None) -> list[str]:
    problems = []
    if files and not view["accepts_files"]:
        problems.append("This assignment does not accept file submissions.")
    if text and not view["accepts_online_text"]:
        problems.append("This assignment does not accept online text.")
    if view["max_files"] and len(files) > view["max_files"]:
        problems.append(f"Too many files ({len(files)}); the limit is {view['max_files']}.")
    for f in files:
        if view["max_file_bytes"] and f.size > view["max_file_bytes"]:
            problems.append(f"{f.name} is {f.size} bytes; the limit is {view['max_file_bytes']}.")
        types = view["file_types"]
        if types and not _type_ok(f.name, types):
            problems.append(f"{f.name}: accepted types are {', '.join(types)}.")
    return problems


def _type_ok(name: str, types: list[str]) -> bool:
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    groups = {
        "image": {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"},
        "document": {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt"},
        "web_image": {".png", ".jpg", ".jpeg", ".gif", ".svg"},
        "archive": {".zip", ".tar", ".gz", ".7z", ".rar"},
        "spreadsheet": {".xls", ".xlsx", ".ods", ".csv"},
        "presentation": {".ppt", ".pptx", ".odp"},
        "source": {".c", ".h", ".py", ".java", ".js", ".cpp"},
    }
    for t in types:
        t = t.lower()
        if t.startswith("."):
            if ext == t:
                return True
        elif t in groups and ext in groups[t]:
            return True
        elif t == "*":
            return True
        elif "/" in t:  # mimetype group (e.g. application/pdf); accept by extension heuristics
            return True
    return False


async def prepare_submission(
    acct: Account, assignment_id: int, files: list[str] | None = None, text: str | None = None
) -> dict[str, Any]:
    """Dry run: what would be sent, whether the assignment accepts it. Sends nothing."""
    view = await get_assignment(acct, assignment_id)
    resolved = await resolve_files(acct, files or [])
    st = await submission_status(acct, assignment_id)
    problems = _check_constraints(view, resolved, text)
    if st["locked"] or not st["can_edit"]:
        problems.append("The submission is locked or not editable now (status: %s)." % st["state"])
    return {
        "assignment": {
            k: view[k]
            for k in (
                "assignment_id",
                "name",
                "due_at",
                "cutoff_at",
                "requires_draft_submit",
                "requires_statement",
                "team_submission",
            )
        },
        "current_state": st["state"],
        "current_files": [f["filename"] for f in st["files"]],
        "would_send": [{"name": f.name, "size": f.size, "sha256": f.sha256} for f in resolved],
        "online_text_chars": len(text) if text else 0,
        "problems": problems,
        "ok": not problems,
        "late_warning": _late(view, acct),
    }


def _late(view: dict, acct: Account) -> str | None:
    from datetime import datetime

    now = datetime.now(acct.tz)
    for key, label in (("cutoff_at", "closed (cutoff)"), ("due_at", "past its due date (will be late)")):
        if view.get(key) and datetime.fromisoformat(view[key]) < now:
            return f"Assignment is {label}."
    return None


async def save_submission(
    acct: Account,
    assignment_id: int,
    files: list[str] | None = None,
    text: str | None = None,
    keep_existing_files: bool = False,
    operation_id: str | None = None,
) -> dict[str, Any]:
    """Upload to the draft area and save the submission *as a draft* (not delivered yet)."""
    if not files and text is None:
        raise ValidationError("Provide files and/or text to save.")
    view = await get_assignment(acct, assignment_id)
    resolved = await resolve_files(acct, files or [])
    problems = _check_constraints(view, resolved, text)
    if problems:
        raise ValidationError(
            "The submission does not fit this assignment's settings.", details={"problems": problems}
        )
    payload_hash = _sha(
        str(assignment_id),
        text or "",
        *[f.sha256 for f in sorted(resolved, key=lambda f: f.name)],
        str(keep_existing_files),
    )
    op_id, prior = await acct.store.begin_operation(
        acct.user_key, acct.profile.name, "save_submission", str(assignment_id), payload_hash, operation_id
    )
    if prior and prior["status"] in ("done", "ambiguous"):
        recon = await _verify_saved(acct, assignment_id, resolved, text)
        return {
            "operation_id": prior["op_id"],
            "already_performed": True,
            "previous_status": prior["status"],
            "verification": recon,
            "note": "An identical save was already performed; nothing was re-sent. Pass a different "
            "operation_id and change the content to force a new save.",
        }

    before = await submission_status(acct, assignment_id)
    if before["locked"] or not before["can_edit"]:
        await acct.store.finish_operation(op_id, "failed", {"reason": "not_editable"})
        raise MoodleAPIError(
            "The submission cannot be edited (locked, closed or already submitted).",
            errorcode="submission_not_editable",
        )
    client = acct.client()
    itemid = 0
    try:
        carried: list[ResolvedFile] = []
        if keep_existing_files:
            for f in before["files"]:
                if f["filename"] not in {r.name for r in resolved}:
                    data, _ = await client.download(f["url"])
                    carried.append(
                        ResolvedFile(f["filename"], data, hashlib.sha256(data).hexdigest(), "moodle")
                    )
        for f in [*carried, *resolved]:
            info = await client.upload_draft(f.name, f.data, itemid=itemid)
            itemid = int(info.get("itemid", itemid))
        plugindata: dict[str, Any] = {}
        if resolved or carried:
            plugindata["files_filemanager"] = itemid
        if text is not None:
            plugindata["onlinetext_editor"] = {"text": text, "format": 1, "itemid": 0}
        res = await client.call(
            "mod_assign_save_submission",
            {"assignmentid": assignment_id, "plugindata": plugindata},
            write=True,
        )
        warnings = [w.get("message") for w in (res or [])]
        if warnings:
            await acct.store.finish_operation(op_id, "failed", {"warnings": warnings})
            raise MoodleAPIError("Moodle refused the save: " + "; ".join(warnings), errorcode="save_warnings")
    except WriteOutcomeUnknownError:
        await acct.store.finish_operation(op_id, "ambiguous", {})
        recon = await _verify_saved(acct, assignment_id, resolved, text)
        return {
            "operation_id": op_id,
            "outcome": "reconciled" if recon["matches"] else "unknown",
            "verification": recon,
            "note": "The connection dropped mid-write. State was re-read instead of re-sending.",
        }
    except MamcpError:
        await acct.store.finish_operation(op_id, "failed", {})
        raise
    verify = await _verify_saved(acct, assignment_id, resolved, text)
    await acct.store.finish_operation(op_id, "done" if verify["matches"] else "ambiguous", verify)
    return {
        "operation_id": op_id,
        "stage": "saved_as_draft",
        "delivered": False,
        "verification": verify,
        "next_step": "Call submit_assignment to deliver it for grading."
        if view["requires_draft_submit"]
        else "This assignment has no separate 'submit' step: saving is the delivery. Check state below.",
    }


async def _verify_saved(
    acct: Account, assignment_id: int, expected: list[ResolvedFile], text: str | None
) -> dict:
    st = await submission_status(acct, assignment_id)
    have = {f["filename"]: f["size"] for f in st["files"]}
    want = {f.name: f.size for f in expected}
    files_ok = all(have.get(n) == s for n, s in want.items())
    text_ok = (
        text is None
        or (st["online_text"] or "").strip() == text.strip()
        or re.sub(r"<[^>]+>", "", st["online_text"] or "").strip() == text.strip()
    )
    return {
        "matches": files_ok and text_ok,
        "state": st["state"],
        "submission_id": st["submission_id"],
        "attempt": st["attempt"],
        "files": st["files"],
        "last_modified": st["last_modified"],
        "online_text_present": st["online_text_present"],
        "read_at": iso(time.time(), acct.tz),
    }


async def submit_assignment(
    acct: Account, assignment_id: int, accept_statement: bool | None = None, operation_id: str | None = None
) -> dict[str, Any]:
    """Final submission for grading. Needed only when the assignment requires a separate submit step."""
    view = await get_assignment(acct, assignment_id)
    before = await submission_status(acct, assignment_id)
    if before["state"] in ("submitted", "graded") and not before["can_edit"]:
        return {"already_submitted": True, "state": before["state"], "evidence": _evidence(before, view)}
    if before["state"] == "not_submitted":
        raise ValidationError("Nothing to submit yet: save a draft first (save_submission).")
    if view["requires_statement"] and not accept_statement:
        raise ValidationError(
            "This assignment requires the student to accept a submission statement (declaration of authorship).",
            hint="Ask the user to confirm the statement, then call again with accept_statement=true. "
            "It is never ticked automatically.",
        )
    if not view["requires_draft_submit"]:
        return {
            "note": "This assignment does not use a separate submit step; the saved submission is already final.",
            "evidence": _evidence(before, view),
        }
    op_id, prior = await acct.store.begin_operation(
        acct.user_key,
        acct.profile.name,
        "submit_for_grading",
        str(assignment_id),
        _sha(str(assignment_id), str(before["attempt"])),
        operation_id,
    )
    if prior and prior["status"] in ("done", "ambiguous"):
        return {
            "operation_id": prior["op_id"],
            "already_performed": True,
            "evidence": _evidence(before, view),
        }
    try:
        res = await acct.client().call(
            "mod_assign_submit_for_grading",
            {"assignmentid": assignment_id, "acceptsubmissionstatement": 1 if accept_statement else 0},
            write=True,
        )
        warnings = [w.get("message") for w in (res or [])]
        if warnings:
            await acct.store.finish_operation(op_id, "failed", {"warnings": warnings})
            raise MoodleAPIError(
                "Moodle refused the submission: " + "; ".join(warnings), errorcode="submit_warnings"
            )
    except WriteOutcomeUnknownError:
        after = await submission_status(acct, assignment_id)
        done = after["state"] in ("submitted", "graded")
        await acct.store.finish_operation(op_id, "done" if done else "ambiguous", _evidence(after, view))
        return {
            "operation_id": op_id,
            "outcome": "reconciled_submitted" if done else "unknown",
            "evidence": _evidence(after, view),
            "note": "Connection dropped; state was re-read, nothing re-sent.",
        }
    except MamcpError:
        await acct.store.finish_operation(op_id, "failed", {})
        raise
    after = await submission_status(acct, assignment_id)
    ok = after["state"] in ("submitted", "graded")
    await acct.store.finish_operation(op_id, "done" if ok else "ambiguous", _evidence(after, view))
    return {"operation_id": op_id, "delivered": ok, "evidence": _evidence(after, view)}


def _evidence(st: dict, view: dict) -> dict[str, Any]:
    return {
        "assignment": view["name"],
        "assignment_id": view["assignment_id"],
        "state": st["state"],
        "submission_id": st["submission_id"],
        "attempt": st["attempt"],
        "files": [{"filename": f["filename"], "size": f["size"]} for f in st["files"]],
        "online_text_present": st["online_text_present"],
        "last_modified": st["last_modified"],
        "due_at": view["due_at"],
        "link": view["link"],
    }


async def deliver_assignment(
    acct: Account,
    assignment_id: int,
    files: list[str] | None = None,
    text: str | None = None,
    accept_statement: bool | None = None,
    keep_existing_files: bool = False,
    operation_id: str | None = None,
) -> dict[str, Any]:
    """Composite: upload -> save -> submit (if the activity needs it) -> read back the persisted state."""
    saved = await save_submission(acct, assignment_id, files, text, keep_existing_files, operation_id)
    if not saved.get("verification", {}).get("matches", False):
        return {
            "delivered": False,
            "stage": "save",
            "detail": saved,
            "message": "Saved content does not match what was sent; not submitting. Inspect and retry.",
        }
    view = await get_assignment(acct, assignment_id)
    if not view["requires_draft_submit"]:
        st = await submission_status(acct, assignment_id)
        return {
            "delivered": st["state"] in ("submitted", "graded", "draft"),
            "stage": "saved",
            "evidence": _evidence(st, view),
            "operation_id": saved.get("operation_id"),
        }
    sub = await submit_assignment(acct, assignment_id, accept_statement)
    return {
        "delivered": bool(sub.get("delivered") or sub.get("already_submitted")),
        "stage": "submitted",
        "save": saved.get("verification"),
        **sub,
    }
