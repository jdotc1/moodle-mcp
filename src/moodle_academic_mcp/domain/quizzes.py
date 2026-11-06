"""Quizzes: listing, attempts and the real attempt protocol (field names come from Moodle's HTML)."""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from html.parser import HTMLParser
from typing import Any

from ..errors import MamcpError, NotFoundError, ValidationError
from ..timeutil import iso
from .account import Account
from .courses import course_ids


async def _journal(acct: Account, kind: str, target: str, evidence: dict, ok: bool = True) -> None:
    """Record a quiz write in the operations journal (each entry unique: never deduplicated).

    Without this, an attempt that ends unexpectedly cannot be traced to the call that closed it. A journal
    failure must never break or mask the quiz call itself, so it is swallowed here on purpose.
    """
    try:
        op_id, _ = await acct.store.begin_operation(
            acct.user_key, acct.profile.name, kind, target, uuid.uuid4().hex
        )
        await acct.store.finish_operation(op_id, "done" if ok else "failed", evidence)
    except Exception:  # noqa: BLE001
        pass


async def quizzes_by_courses(acct: Account, ids: list[int], attempts: bool = False) -> list[dict]:
    if not ids:
        return []
    raw = await acct.client().call("mod_quiz_get_quizzes_by_courses", {"courseids": ids})
    uid = await acct.userid() if attempts else 0
    out = []
    sem = asyncio.Semaphore(4)

    async def enrich(q: dict) -> dict:
        v = {
            "quiz_id": q["id"],
            "cmid": q.get("coursemodule"),
            "course_id": q["course"],
            "name": q["name"],
            "opens_ts": q.get("timeopen") or None,
            "closes_ts": q.get("timeclose") or None,
            "opens_at": iso(q.get("timeopen"), acct.tz),
            "closes_at": iso(q.get("timeclose"), acct.tz),
            "time_limit_seconds": q.get("timelimit") or None,
            "attempts_allowed": q.get("attempts") or None,
            "grade_method": q.get("grademethod"),
            "navigation": q.get("navmethod"),
            "requires_password": bool(q.get("password")) if "password" in q else None,
            "link": acct.link(f"mod/quiz/view.php?id={q.get('coursemodule')}"),
            "finished_attempts": 0,
            "attempt_state": "unknown",
            "in_progress_attempt_id": None,
        }
        if attempts:
            async with sem:
                try:
                    res = await acct.client().call(
                        "mod_quiz_get_user_attempts",
                        {"quizid": q["id"], "userid": uid, "status": "all", "includepreviews": 0},
                    )
                    atts = res.get("attempts", [])
                    v["finished_attempts"] = sum(1 for a in atts if a["state"] == "finished")
                    prog = [a for a in atts if a["state"] == "inprogress"]
                    v["in_progress_attempt_id"] = prog[0]["id"] if prog else None
                    v["attempt_state"] = (
                        "in_progress" if prog else "finished" if v["finished_attempts"] else "not_attempted"
                    )
                except MamcpError:
                    pass
        return v

    for c in raw.get("quizzes", []):
        out.append(c)
    return list(await asyncio.gather(*[enrich(q) for q in out]))


async def list_quizzes(acct: Account, course_id: int | None = None) -> dict[str, Any]:
    ids = [course_id] if course_id else await course_ids(acct)
    items = await quizzes_by_courses(acct, ids, attempts=True)
    for i in items:
        i.pop("opens_ts"), i.pop("closes_ts")
    return {"items": items, "total": len(items)}


async def quiz_info(acct: Account, quiz_id: int) -> dict[str, Any]:
    uid = await acct.userid()
    c = acct.client()
    acc, atts = await asyncio.gather(
        c.call("mod_quiz_get_quiz_access_information", {"quizid": quiz_id}),
        c.call(
            "mod_quiz_get_user_attempts",
            {"quizid": quiz_id, "userid": uid, "status": "all", "includepreviews": 0},
        ),
    )
    return {
        "quiz_id": quiz_id,
        "can_attempt": acc.get("canattempt"),
        "prevent_access_reasons": acc.get("preventaccessreasons", []),
        "access_rules": acc.get("accessrules", []),
        "attempts": [
            {
                "attempt_id": a["id"],
                "number": a.get("attempt"),
                "state": a["state"],
                "started_at": iso(a.get("timestart"), acct.tz),
                "finished_at": iso(a.get("timefinish"), acct.tz),
                "grade": a.get("sumgrades"),
            }
            for a in atts.get("attempts", [])
        ],
        "note": "Starting an attempt consumes one allowed attempt and may start a countdown (start_quiz_attempt).",
    }


# ----------------------------------------------------------------- HTML form extraction
class _Form(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.inputs: list[dict] = []
        self.text: list[str] = []
        self._select: dict | None = None
        self._opt: dict | None = None
        self._label_for: str | None = None
        self.labels: dict[str, str] = {}
        self._cur_label: list[str] | None = None
        self._textarea: dict | None = None
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "input":
            self.inputs.append(
                {
                    "tag": "input",
                    "type": a.get("type", "text"),
                    "name": a.get("name", ""),
                    "value": a.get("value", ""),
                    "id": a.get("id", ""),
                    "checked": "checked" in a,
                    "disabled": "disabled" in a or "readonly" in a,
                }
            )
        elif tag == "select":
            self._select = {
                "tag": "select",
                "type": "select",
                "name": a.get("name", ""),
                "value": "",
                "options": [],
                "id": a.get("id", ""),
                "disabled": "disabled" in a,
            }
        elif tag == "option" and self._select is not None:
            self._opt = {"value": a.get("value", ""), "label": "", "selected": "selected" in a}
        elif tag == "textarea":
            self._textarea = {
                "tag": "textarea",
                "type": "textarea",
                "name": a.get("name", ""),
                "value": "",
                "id": a.get("id", ""),
                "disabled": "disabled" in a or "readonly" in a,
            }
        elif tag == "label":
            self._label_for = a.get("for")
            self._cur_label = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag == "option" and self._select is not None and self._opt is not None:
            self._opt["label"] = self._opt["label"].strip()
            self._select["options"].append(self._opt)
            if self._opt["selected"]:
                self._select["value"] = self._opt["value"]
            self._opt = None
        elif tag == "select" and self._select is not None:
            self.inputs.append(self._select)
            self._select = None
        elif tag == "textarea" and self._textarea is not None:
            self.inputs.append(self._textarea)
            self._textarea = None
        elif tag == "label" and self._cur_label is not None:
            if self._label_for:
                self.labels[self._label_for] = " ".join("".join(self._cur_label).split())
            self._cur_label = None

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._opt is not None:
            self._opt["label"] += data
        elif self._textarea is not None:
            self._textarea["value"] += data
        else:
            if self._cur_label is not None:
                self._cur_label.append(data)
            self.text.append(data)


def parse_question_html(html: str) -> dict[str, Any]:
    p = _Form()
    p.feed(html)
    fields: list[dict] = []
    seq: dict[str, str] = {}
    for i in p.inputs:
        n = i["name"]
        if not n:
            continue
        if n.endswith(":sequencecheck"):
            seq[n] = i["value"]
            continue
        if i["type"] == "hidden" and (n.endswith(":flagged") or "_:" in n and n.endswith("-submit")):
            continue
        if i["type"] in ("submit", "button", "image"):
            continue
        f = {
            "name": n,
            "kind": i["type"],
            "current": i["value"] if i["type"] not in ("radio", "checkbox") else None,
            "disabled": i.get("disabled", False),
        }
        if i["type"] == "hidden" and re.search(r"_:?\w*(choice|answer)\b", n) and i["value"] in ("0", "-1"):
            continue  # Moodle's "clear choice"/unchecked placeholder hidden inputs
        if i["type"] in ("radio", "checkbox"):
            lbl = p.labels.get(i["id"], "")
            f["option"] = {"value": i["value"], "label": lbl, "checked": i["checked"]}
        if i["type"] == "select":
            f["options"] = [
                {"value": o["value"], "label": o["label"]} for o in i["options"] if o["value"] != ""
            ]
        fields.append(f)
    # merge radios/checkboxes with the same name
    merged: dict[str, dict] = {}
    order: list[str] = []
    for f in fields:
        if f["kind"] in ("radio", "checkbox"):
            key = f["name"] if f["kind"] == "radio" else f["name"]
            if key not in merged:
                merged[key] = {
                    "name": f["name"],
                    "kind": f["kind"],
                    "options": [],
                    "current": None,
                    "disabled": f["disabled"],
                }
                order.append(key)
            merged[key]["options"].append({"value": f["option"]["value"], "label": f["option"]["label"]})
            if f["option"]["checked"]:
                merged[key]["current"] = f["option"]["value"]
        elif f["kind"] == "hidden":
            continue
        else:
            merged[f["name"] + "#" + str(len(order))] = f
            order.append(f["name"] + "#" + str(len(order)))
    text = " ".join(" ".join(p.text).split())
    return {"text": text, "fields": [merged[k] for k in order], "sequence_checks": seq}


def _view_question(q: dict, acct: Account) -> dict[str, Any]:
    parsed = parse_question_html(q.get("html", ""))
    supported = bool(parsed["fields"]) and not q.get("blockedbyprevious")
    return {
        "slot": q.get("slot"),
        "number": q.get("number"),
        "type": q.get("type"),
        "page": q.get("page"),
        "state": q.get("state"),
        "status": q.get("status"),
        "max_mark": q.get("maxmark"),
        "flagged": bool(q.get("flagged")),
        "blocked_by_previous": bool(q.get("blockedbyprevious")),
        "statement": parsed["text"],
        "fields": parsed["fields"],
        "sequence_checks": parsed["sequence_checks"],
        "supported_by_form_protocol": supported,
        "note": None
        if supported
        else "No plain form fields found (interactive/JS question type or blocked): "
        "answer it in the browser.",
    }


async def _attempt_page(
    acct: Account, attempt_id: int, page: int, preflight: dict | None = None
) -> dict[str, Any]:
    params: dict[str, Any] = {"attemptid": attempt_id, "page": page}
    if preflight:
        params["preflightdata"] = [{"name": k, "value": v} for k, v in preflight.items()]
    res = await acct.client().call("mod_quiz_get_attempt_data", params)
    att = res.get("attempt", {})
    return {
        "attempt_id": att.get("id", attempt_id),
        "state": att.get("state"),
        "page": page,
        "next_page": res.get("nextpage") if res.get("nextpage", -1) != -1 else None,
        "started_at": iso(att.get("timestart"), acct.tz),
        "current_page": att.get("currentpage"),
        "questions": [_view_question(q, acct) for q in res.get("questions", [])],
        "messages": res.get("messages", []),
    }


async def start_attempt(
    acct: Account, quiz_id: int, password: str | None = None, resume_if_in_progress: bool = True
) -> dict[str, Any]:
    """WRITE: starts a new attempt (consumes an attempt, may start a countdown). Resumes an open one when asked."""
    uid = await acct.userid()
    atts = await acct.client().call(
        "mod_quiz_get_user_attempts",
        {"quizid": quiz_id, "userid": uid, "status": "unfinished", "includepreviews": 0},
    )
    open_attempts = [a for a in atts.get("attempts", []) if a["state"] == "inprogress"]
    pre = {"quizpassword": password} if password else None
    if open_attempts and resume_if_in_progress:
        a = open_attempts[0]
        page = await _attempt_page(acct, a["id"], a.get("currentpage") or 0, pre)
        return {"resumed": True, "attempt_id": a["id"], "first_page": page}
    params: dict[str, Any] = {"quizid": quiz_id}
    if pre:
        params["preflightdata"] = [{"name": k, "value": v} for k, v in pre.items()]
    res = await acct.client().call("mod_quiz_start_attempt", params, write=True)
    att = res.get("attempt", {})
    await _journal(
        acct, "quiz_start", str(att.get("id")), {"quiz_id": quiz_id, "state_after": att.get("state")}
    )
    page = await _attempt_page(acct, att["id"], 0, pre)
    return {
        "resumed": False,
        "attempt_id": att["id"],
        "started_at": iso(att.get("timestart"), acct.tz),
        "first_page": page,
        "warnings": [w.get("message") for w in res.get("warnings", [])],
        "note": "The attempt is now open on the server. Timers (if any) run server-side.",
    }


async def get_attempt_page(
    acct: Account, attempt_id: int, page: int = 0, password: str | None = None
) -> dict:
    return await _attempt_page(acct, attempt_id, page, {"quizpassword": password} if password else None)


async def attempt_summary(acct: Account, attempt_id: int) -> dict[str, Any]:
    res = await acct.client().call("mod_quiz_get_attempt_summary", {"attemptid": attempt_id})
    return {
        "attempt_id": attempt_id,
        "questions": [
            {
                "slot": q.get("slot"),
                "number": q.get("number"),
                "state": q.get("state"),
                "status": q.get("status"),
                "flagged": bool(q.get("flagged")),
                "page": q.get("page"),
            }
            for q in res.get("questions", [])
        ],
    }


async def save_answers(
    acct: Account,
    attempt_id: int,
    page: int,
    responses: dict[str, str],
    finish: bool = False,
    password: str | None = None,
) -> dict[str, Any]:
    """Save (and optionally finish) using the exact field names Moodle returned for `page`.

    Unknown field names are rejected (no invented protocol). `sequencecheck` fields are attached automatically
    from a fresh read of the page, as Moodle requires.
    """
    pre = {"quizpassword": password} if password else None
    current = await _attempt_page(acct, attempt_id, page, pre)
    if current["state"] != "inprogress":
        await _journal(
            acct,
            "quiz_save",
            str(attempt_id),
            {
                "page": page,
                "fields": responses,
                "finish_requested": finish,
                "state_before": current["state"],
                "error": "attempt_not_in_progress",
            },
            ok=False,
        )
        raise ValidationError(
            f"Attempt {attempt_id} is {current['state']!r}, not in progress: these answers were NOT saved.",
            details={"state": current["state"]},
        )
    valid: dict[str, dict] = {}
    seqs: dict[str, str] = {}
    for q in current["questions"]:
        for f in q["fields"]:
            valid[f["name"]] = f
        seqs.update(q["sequence_checks"])
    unknown = [n for n in responses if n not in valid]
    if unknown:
        raise ValidationError(
            "Unknown field names for this page.", details={"unknown": unknown, "valid_fields": sorted(valid)}
        )
    for n, v in responses.items():
        f = valid[n]
        opts = f.get("options")
        if opts and f["kind"] in ("radio", "select") and str(v) not in {o["value"] for o in opts}:
            raise ValidationError(f"Value {v!r} is not an option of {n}.", details={"options": opts})
        if f.get("disabled"):
            raise ValidationError(f"Field {n} is read-only.")
    # Moodle needs the sequencecheck of every question being saved (prefix "q<usage>:<slot>").
    touched = {n.split("_", 1)[0] for n in responses}
    data = [{"name": n, "value": str(v)} for n, v in responses.items()]
    for sn, sv in seqs.items():
        if sn.split("_", 1)[0] in touched:
            data.append({"name": sn, "value": sv})
    params: dict[str, Any] = {"attemptid": attempt_id, "data": data, "finishattempt": 1 if finish else 0}
    if pre:
        params["preflightdata"] = [{"name": k, "value": v} for k, v in pre.items()]
    journal = {
        "page": page,
        "fields": responses,
        "finish_requested": finish,
        "state_before": current["state"],
    }
    try:
        res = await acct.client().call("mod_quiz_process_attempt", params, write=True)
    except MamcpError as e:
        await _journal(acct, "quiz_save", str(attempt_id), {**journal, "error": e.code}, ok=False)
        raise
    await _journal(
        acct,
        "quiz_save",
        str(attempt_id),
        {**journal, "state_after": res.get("state") if isinstance(res, dict) else None},
    )
    out: dict[str, Any] = {
        "attempt_id": attempt_id,
        "saved_fields": sorted(responses),
        "finish_requested": finish,
        "state": res.get("state") if isinstance(res, dict) else None,
        "warnings": [w.get("message") for w in (res or {}).get("warnings", [])]
        if isinstance(res, dict)
        else [],
    }
    after = await attempt_summary(acct, attempt_id)
    out["summary_after"] = after
    if finish:
        out["finished"] = out["state"] == "finished"
        out["note"] = "Finishing is final. Read the persisted state with attempt_summary / quiz_info."
    return out


async def finish_attempt(acct: Account, attempt_id: int) -> dict[str, Any]:
    """WRITE: submit all answers for grading and close the attempt (no further changes)."""
    try:
        res = await acct.client().call(
            "mod_quiz_process_attempt", {"attemptid": attempt_id, "data": [], "finishattempt": 1}, write=True
        )
    except MamcpError as e:
        await _journal(acct, "quiz_finish", str(attempt_id), {"error": e.code}, ok=False)
        raise
    state = res.get("state") if isinstance(res, dict) else None
    await _journal(acct, "quiz_finish", str(attempt_id), {"state_after": state})
    return {
        "attempt_id": attempt_id,
        "finished": state == "finished",
        "state": state,
        "warnings": [w.get("message") for w in (res or {}).get("warnings", [])]
        if isinstance(res, dict)
        else [],
        "read_at": iso(time.time(), acct.tz),
    }


async def find_quiz(acct: Account, quiz_id: int) -> dict:
    for q in await quizzes_by_courses(acct, await course_ids(acct, False), attempts=False):
        if q["quiz_id"] == quiz_id:
            return q
    raise NotFoundError(f"Quiz {quiz_id} not found.")
