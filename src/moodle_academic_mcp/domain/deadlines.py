"""Cross-module deadline discovery. The calendar alone is never treated as complete."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from ..errors import MamcpError
from ..timeutil import iso, window
from .account import Account, paginate
from .assignments import _fetch_assignments, _view, statuses_for
from .courses import list_courses
from .quizzes import quizzes_by_courses


async def calendar_action_events(
    acct: Account, ts_from: int, ts_to: int, course_id: int | None = None
) -> list[dict]:
    """Exhaust core_calendar_get_action_events_by_timesort (cursor pagination by aftereventid)."""
    out: list[dict] = []
    after = 0
    for _ in range(40):
        params: dict[str, Any] = {"timesortfrom": ts_from, "timesortto": ts_to, "limitnum": 50}
        if after:
            params["aftereventid"] = after
        res = await acct.client().call("core_calendar_get_action_events_by_timesort", params)
        evs = res.get("events", [])
        out.extend(evs)
        if len(evs) < 50 or not res.get("lastid"):
            break
        after = res["lastid"]
    if course_id:
        out = [e for e in out if (e.get("course") or {}).get("id") == course_id]
    return out


def _match(a: dict, ts_from: int, ts_to: int) -> str:
    """Which date of the assignment put it in the window: due | cutoff | past_due (open, before the window)."""
    if a.get("duedate") and ts_from <= a["duedate"] < ts_to:
        return "due"
    if a.get("cutoffdate") and ts_from <= a["cutoffdate"] < ts_to:
        return "cutoff (the due date is outside this window)"
    return "past_due"


async def list_deadlines(
    acct: Account,
    range: str = "next_7_days",
    start: str | None = None,
    end: str | None = None,
    course_id: int | None = None,
    status: str = "all",
    types: list[str] | None = None,
    include_past_due: bool = False,
    past_due_days: int = 30,
    offset: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """Everything with a date in the window: assignments, quizzes and other calendar action events.

    range: today | this_week | next_week | next_7_days | next_30_days | custom (start/end ISO)
    status: all | submitted | pending
    """
    w = window(range, acct.tz, start=start, end=end)
    ts_from, ts_to = w.ts
    courses = (await list_courses(acct, True, 0, 200))["items"]
    by_id = {c["id"]: c for c in courses}
    cids = [course_id] if course_id else list(by_id)
    items: list[dict[str, Any]] = []
    sources_errors: list[dict] = []
    seen: set[tuple[str, Any]] = set()

    # 1) assignments
    try:
        pairs = await _fetch_assignments(acct, cids) if cids else []
    except MamcpError as e:
        pairs = []
        sources_errors.append({"source": "mod_assign_get_assignments", **e.to_dict()})
    wanted = []
    for c, a in pairs:
        dates = [d for d in (a.get("duedate"), a.get("cutoffdate")) if d]
        if any(ts_from <= d < ts_to for d in dates) or (
            include_past_due
            and a.get("duedate")
            and ts_from - past_due_days * 86400 <= a["duedate"] < ts_from
            and a.get("cutoffdate", 0) in (0, None)
        ):
            wanted.append((c, a))
    statuses = await statuses_for(acct, [a["id"] for _, a in wanted])
    for c, a in wanted:
        v = _view(acct, a, c)
        st = statuses.get(a["id"], {})
        eff = st.get("extension_due_at") or v["due_at"]
        seen.add(("assign", a["id"]))
        seen.add(
            ("assign", a.get("cmid"))
        )  # calendar events carry the cmid in `instance` (seen on Moodle 4.1)
        items.append(
            {
                "type": "assignment",
                "id": a["id"],
                "course_id": a.get("course"),
                "course": by_id.get(a.get("course"), {}).get("shortname") or c.get("shortname"),
                "name": a["name"],
                "due_at": v["due_at"],
                "effective_due_at": eff,
                "cutoff_at": v["cutoff_at"],
                "window_match": _match(a, ts_from, ts_to),
                "extension_applied": bool(st.get("extension_due_at")),
                "submission_state": st.get("state", "unknown"),
                "group_assignment": v["team_submission"],
                "submitted": st.get("state") in ("submitted", "graded"),
                "link": v["link"],
                "source": "mod_assign",
                "notes": v["date_notes"][:1] if v["cutoff_at"] and v["cutoff_at"] != v["due_at"] else [],
            }
        )

    # 2) quizzes
    try:
        for q in await quizzes_by_courses(acct, cids, attempts=True):
            closes = q.get("closes_ts")
            if closes and ts_from <= closes < ts_to or q.get("opens_ts") and ts_from <= q["opens_ts"] < ts_to:
                seen.add(("quiz", q["quiz_id"]))
                seen.add(("quiz", q.get("cmid")))
                items.append(
                    {
                        "type": "quiz",
                        "id": q["quiz_id"],
                        "course_id": q["course_id"],
                        "course": by_id.get(q["course_id"], {}).get("shortname"),
                        "name": q["name"],
                        "opens_at": q["opens_at"],
                        "due_at": q["closes_at"],
                        "effective_due_at": q["closes_at"],
                        "cutoff_at": q["closes_at"],
                        "submission_state": q["attempt_state"],
                        "submitted": q["finished_attempts"] > 0,
                        "link": q["link"],
                        "source": "mod_quiz",
                        "notes": [
                            f"{q['finished_attempts']} of {q['attempts_allowed'] or 'unlimited'} attempts used"
                        ],
                    }
                )
    except MamcpError as e:
        sources_errors.append({"source": "mod_quiz_get_quizzes_by_courses", **e.to_dict()})

    # 3) other action events (forums, lessons, workshops, choices...) - de-duplicated against the above
    try:
        for ev in await calendar_action_events(acct, ts_from, ts_to, course_id):
            mod = ev.get("modulename")
            key = (mod, ev.get("instance"))
            if key in seen:
                continue
            items.append(
                {
                    "type": mod or ev.get("eventtype"),
                    "id": ev.get("instance"),
                    "course_id": (ev.get("course") or {}).get("id"),
                    "course": (ev.get("course") or {}).get("shortname"),
                    "name": ev.get("name"),
                    "due_at": iso(ev.get("timesort"), acct.tz),
                    "effective_due_at": iso(ev.get("timesort"), acct.tz),
                    "submission_state": "unknown",
                    "submitted": None,
                    "actionable": (ev.get("action") or {}).get("actionable"),
                    "link": ev.get("url"),
                    "source": "calendar",
                    "notes": ["From the calendar only: completion state not checked for this module type."],
                }
            )
    except MamcpError as e:
        sources_errors.append({"source": "core_calendar_get_action_events_by_timesort", **e.to_dict()})

    if types:
        items = [i for i in items if i["type"] in types]
    if status == "submitted":
        items = [i for i in items if i.get("submitted") is True]
    elif status == "pending":
        items = [i for i in items if i.get("submitted") is not True]
    items.sort(key=lambda i: (i.get("effective_due_at") or "9999", i["name"] or ""))
    res = paginate(items, offset, limit)
    res.update(
        window=w.to_dict(),
        now=datetime.now(acct.tz).isoformat(),
        course_filter=course_id,
        status_filter=status,
        sources_errors=sources_errors,
        coverage=(
            "assignments + quizzes + calendar action events; other module types without a calendar "
            "entry cannot be detected"
        ),
        generated_at=iso(time.time(), acct.tz),
    )
    return res
