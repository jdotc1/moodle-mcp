"""Attendance: adapter for mod_attendance web services + manual import, feeding calc.attendance."""

from __future__ import annotations

import time
from datetime import date, datetime
from fractions import Fraction
from typing import Any

from ..calc.attendance import Policy, Session, analyze, parse_min_frequency, to_fraction
from ..errors import MamcpError, UnsupportedError, ValidationError
from ..timeutil import iso, parse_dt, to_dt
from .account import Account
from .courses import raw_modules

# Attendance status sets are configurable per instance: map by acronym or (localised) description.
STATUS_WORDS = {
    "present": {"p", "presente", "present"},
    "late": {"l", "a atrasado", "atrasado", "late", "atraso"},
    "excused": {"e", "justificado", "excused", "abonado", "abono", "falta justificada"},
    "absent": {"a", "ausente", "absent", "falta", "f"},
}


def categorize(status: dict) -> str:
    for key in (
        str(status.get("acronym", "")).strip().lower(),
        str(status.get("description", "")).strip().lower(),
    ):
        for cat, words in STATUS_WORDS.items():
            if key in words:
                return cat
    return "unknown"


async def find_instances(acct: Account, course_id: int) -> list[dict]:
    return [
        {"cmid": m["id"], "attendance_id": m.get("instance"), "name": m["name"], "link": m.get("url")}
        for m in await raw_modules(acct, course_id)
        if m["modname"] == "attendance"
    ]


async def _moodle_sessions(
    acct: Account, course_id: int, attendance_id: int | None, hours_per_session: Any, minutes_per_hour: Any
) -> tuple[list[Session], dict[str, Any]]:
    if "attendance" not in acct.tokens:
        raise UnsupportedError(
            "No 'attendance' token configured.",
            hint="Create a token for the Attendance service in /user/managetoken.php and run "
            "`configure`, or use attendance_import_manual.",
        )
    ident = await acct.require_verified_identity("attendance")
    if not ident["usable"]:
        raise UnsupportedError(
            "Cannot prove that the Attendance token belongs to the same Moodle user as your main token "
            f"({ident['evidence']}). Records are not read to avoid mixing identities.",
            hint=ident.get("how_to_attest", "Use attendance_import_manual."),
        )
    if attendance_id is None:
        inst = await find_instances(acct, course_id)
        if not inst:
            raise UnsupportedError("No Attendance activity found in this course.")
        if len(inst) > 1:
            raise ValidationError(
                "Several Attendance activities; pass attendance_id.", details={"instances": inst}
            )
        attendance_id = inst[0]["attendance_id"]
    c = acct.client("attendance")
    my_uid = ident["userid"]
    sessions_raw = await c.call("mod_attendance_get_sessions", {"attendanceid": attendance_id})
    now = time.time()
    out: list[Session] = []
    last_taken = 0
    unknown_status = set()
    for s in sessions_raw:
        sid = s["id"]
        when = to_dt(s.get("sessdate"), acct.tz)
        dur_s = s.get("duration") or 0
        if hours_per_session is not None:
            w = to_fraction(hours_per_session)
        elif minutes_per_hour is not None:
            w = Fraction(dur_s, 60) / to_fraction(minutes_per_hour)
        else:
            w = Fraction(1)
        taken = s.get("lasttaken") or 0
        last_taken = max(last_taken, taken)
        if (s.get("sessdate") or 0) > now:
            cat = "future"
        elif not taken:
            cat = "pending"
        else:
            det = await c.call("mod_attendance_get_session", {"sessionid": sid})
            statuses = {str(st["id"]): st for st in det.get("statuses", [])}
            mine = [lg for lg in det.get("attendance_log", []) if int(lg.get("studentid", -1)) == my_uid]
            if not mine:
                cat = "pending"  # roll call taken but no record for this student: not an absence by inference
            else:
                st = statuses.get(str(mine[-1].get("statusid")))
                cat = categorize(st) if st else "unknown"
                if cat == "unknown":
                    unknown_status.add(str(st.get("description") if st else mine[-1].get("statusid")))
        out.append(Session(str(sid), when.date() if when else None, w, cat, s.get("description") or ""))
    meta = {
        "source": "moodle:mod_attendance",
        "attendance_id": attendance_id,
        "last_roll_call_at": iso(last_taken, acct.tz) if last_taken else None,
        "identity": ident["trust"],
        "unknown_statuses": sorted(unknown_status),
        "status": "documented_upstream_unvalidated",
    }
    return out, meta


async def import_manual(
    acct: Account, course_id: int, sessions: list[dict], total_hours: Any = None, note: str = ""
) -> dict[str, Any]:
    """Store sessions typed/pasted by the user (clearly labelled as manual)."""
    clean = []
    for i, s in enumerate(sessions):
        cat = s.get("status", "present")
        if cat not in ("present", "late", "absent", "excused", "cancelled", "pending", "future"):
            raise ValidationError(f"session {i}: unknown status {cat!r}")
        if s.get("date"):
            date.fromisoformat(s["date"])
        clean.append(
            {
                "id": str(s.get("id", f"m{i + 1}")),
                "date": s.get("date"),
                "hours": str(s.get("hours", 1)),
                "status": cat,
                "label": s.get("label", ""),
            }
        )
    payload = {
        "sessions": clean,
        "total_hours": None if total_hours is None else str(total_hours),
        "note": note,
        "imported_at": iso(time.time(), acct.tz),
        "origin": "manual",
    }
    await acct.store.put_manual_attendance(acct.user_key, acct.profile.name, course_id, payload)
    return {
        "stored": len(clean),
        "origin": "manual",
        "note": "These records are user-supplied, not official.",
    }


async def report(
    acct: Account,
    course_id: int,
    source: str = "auto",
    attendance_id: int | None = None,
    min_frequency: Any = None,
    rule_source: str | None = None,
    total_hours: Any = None,
    hours_per_session: Any = None,
    minutes_per_hour: Any = None,
    late_counts_as: str = "present",
    excused_counts_as: str = "absent",
    skip_dates: list[str] | None = None,
    assume_future_attended: Any = 1,
    assume_pending: str = "unknown",
) -> dict[str, Any]:
    sessions: list[Session] = []
    meta: dict[str, Any] = {}
    errors: list[dict] = []
    manual = await acct.store.get_manual_attendance(acct.user_key, acct.profile.name, course_id)
    if source in ("auto", "moodle"):
        try:
            sessions, meta = await _moodle_sessions(
                acct, course_id, attendance_id, hours_per_session, minutes_per_hour
            )
        except MamcpError as e:
            errors.append(e.to_dict())
            if source == "moodle":
                raise
    if not sessions and source in ("auto", "manual"):
        if not manual:
            raise UnsupportedError(
                "No attendance data available from Moodle and no manual import.",
                hint="Use attendance_import_manual with the sessions you know, or configure the Attendance token.",
                details={"moodle_errors": errors},
            )
        sessions = [
            Session(
                s["id"],
                date.fromisoformat(s["date"]) if s.get("date") else None,
                to_fraction(s["hours"]),
                s["status"],
                s.get("label", ""),
            )
            for s in manual["sessions"]
        ]
        meta = {
            "source": "manual import",
            "imported_at": manual["imported_at"],
            "note": manual.get("note"),
            "status": "user_supplied",
        }
        if total_hours is None and manual.get("total_hours"):
            total_hours = manual["total_hours"]
    # Rule: argument > profile default. No silent universal constant.
    mf = min_frequency if min_frequency is not None else acct.profile.attendance_min_percent
    src = rule_source or acct.profile.attendance_rule_source or ""
    if mf is None:
        raise ValidationError(
            "The minimum frequency is a per-course rule and was not provided.",
            hint="Pass min_frequency (e.g. '75%') and rule_source (e.g. 'plano de ensino 2026/2, item 7'); or set "
            "attendance_min_percent / attendance_rule_source on the profile.",
        )
    policy = Policy(
        parse_min_frequency(mf),
        late_counts_as,
        excused_counts_as,
        None if total_hours is None else to_fraction(total_hours),
        src,
    )
    skip_ids = set()
    for d in skip_dates or []:
        dd = parse_dt(d, acct.tz).date()
        skip_ids |= {s.id for s in sessions if s.when == dd and s.category in ("future", "pending")}
        if not any(s.when == dd for s in sessions):
            meta.setdefault("warnings", []).append(
                f"No listed session on {d}; nothing to skip for that date."
            )
    res = analyze(
        sessions,
        policy,
        skip_ids=skip_ids,
        assume_pending=assume_pending,
        assume_future=to_fraction(assume_future_attended),
    )
    res["data_source"] = meta
    res["weight_basis"] = (
        "hours_per_session=%s" % hours_per_session
        if hours_per_session is not None
        else "duration/minutes_per_hour=%s" % minutes_per_hour
        if minutes_per_hour is not None
        else "each meeting counts 1 unit (no hours given)"
    )
    res["not_the_same_as"] = [
        "Attendance points/percentage in the gradebook",
        "activity completion",
        "the institution's official attendance record",
    ]
    res["sessions"] = [
        {
            "id": s.id,
            "date": s.when.isoformat() if s.when else None,
            "weight": float(s.weight),
            "status": s.category,
        }
        for s in sessions
    ]
    if errors:
        res["moodle_errors"] = errors
    res["generated_at"] = datetime.now(acct.tz).isoformat()
    return res
