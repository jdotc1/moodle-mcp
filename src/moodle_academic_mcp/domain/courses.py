"""Courses, structure, completion."""

from __future__ import annotations

import re
from typing import Any

from ..redact import strip_url_credentials
from ..timeutil import iso
from .account import Account, paginate

_SEM = re.compile(r"\((\d{4}[1-3]?)\)")


def semester_hint(shortname: str, startdate: int | None, acct: Account) -> dict[str, str] | None:
    m = _SEM.search(shortname or "")
    if m:
        code = m.group(1)
        return {"value": code, "source": "parsed from course shortname (heuristic)"}
    if startdate:
        from ..timeutil import to_dt

        d = to_dt(startdate, acct.tz)
        assert d is not None
        return {
            "value": f"{d.year}/{1 if d.month <= 6 else 2}",
            "source": "derived from course start date (heuristic)",
        }
    return None


async def list_courses(
    acct: Account, only_current: bool = True, offset: int = 0, limit: int = 50, semester: str | None = None
) -> dict[str, Any]:
    import time

    uid = await acct.userid()
    raw = await acct.client().call("core_enrol_get_users_courses", {"userid": uid})
    now = time.time()
    out = []
    for c in raw:
        end = c.get("enddate") or 0
        out.append(
            {
                "id": c["id"],
                "shortname": c.get("shortname"),
                "fullname": c.get("fullname"),
                "idnumber": c.get("idnumber") or None,
                "semester": semester_hint(c.get("shortname", ""), c.get("startdate"), acct),
                "start": iso(c.get("startdate"), acct.tz),
                "end": iso(end, acct.tz) if end else None,
                "progress": c.get("progress"),
                "hidden": c.get("visible") == 0,
                "link": acct.link(f"course/view.php?id={c['id']}"),
                "_ended": bool(end and end < now) or c.get("completed") is True,
            }
        )
    codes = sorted(
        {
            o["semester"]["value"]
            for o in out
            if o["semester"] and o["semester"]["value"].isdigit() and len(o["semester"]["value"]) == 5
        }
    )
    latest = codes[-1] if codes else None
    if semester:
        out = [o for o in out if o["semester"] and o["semester"]["value"] == semester]
    elif only_current:
        # Sites often keep old terms enrolled with enddate=0: current = not ended AND in the latest term seen.
        out = [
            o
            for o in out
            if not o["_ended"] and (latest is None or not o["semester"] or o["semester"]["value"] == latest)
        ]
    for o in out:
        o.pop("_ended")
    res = paginate(out, offset, limit)
    res["filter"] = {"only_current": only_current, "semester": semester, "latest_term_seen": latest}
    res["hint"] = (
        "Older terms are hidden; pass only_current=false or semester=<code> (e.g. 20252) to see them."
        if only_current and not semester
        else None
    )
    res["timezone"] = str(acct.tz)
    return res


async def course_ids(acct: Account, only_current: bool = True) -> list[int]:
    return [c["id"] for c in (await list_courses(acct, only_current, 0, 200))["items"]]


def _module_view(acct: Account, m: dict) -> dict[str, Any]:
    cd = m.get("completiondata") or {}
    files = []
    for i, f in enumerate(m.get("contents") or []):
        if f.get("type") == "file":
            files.append(
                {
                    "index": i,
                    "filename": f.get("filename"),
                    "size": f.get("filesize"),
                    "mimetype": f.get("mimetype"),
                    "modified": iso(f.get("timemodified"), acct.tz),
                }
            )
        elif f.get("type") == "url":
            files.append(
                {
                    "index": i,
                    "external_url": strip_url_credentials(f.get("fileurl", "")),
                    "filename": f.get("filename"),
                }
            )
    return {
        "cmid": m["id"],
        "instance": m.get("instance"),
        "type": m.get("modname"),
        "name": m.get("name"),
        "visible_to_me": m.get("uservisible", True),
        "link": m.get("url") or None,
        "completion": None
        if not cd
        else {
            "state": cd.get("state"),
            "tracking": cd.get("tracking"),
            "complete_by": iso(cd.get("timecompleted"), acct.tz),
        },
        "dates": [
            {"label": d.get("label"), "at": iso(d.get("timestamp"), acct.tz)} for d in m.get("dates", [])
        ],
        "files": files,
        "description_html": m.get("description"),
    }


async def course_contents(
    acct: Account,
    course_id: int,
    query: str | None = None,
    section: int | None = None,
    offset: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    raw = await acct.client().call("core_course_get_contents", {"courseid": course_id})
    mods: list[dict] = []
    sections = []
    for s in raw:
        if section is not None and s.get("section") != section:
            continue
        sections.append({"section": s.get("section"), "name": s.get("name"), "visible": s.get("visible", 1)})
        for m in s.get("modules", []):
            v = _module_view(acct, m)
            v["section"] = s.get("section")
            v["section_name"] = s.get("name")
            if (
                query
                and query.lower() not in (v["name"] or "").lower()
                and not any(query.lower() in (f.get("filename") or "").lower() for f in v["files"])
            ):
                continue
            mods.append(v)
    res = paginate(mods, offset, limit)
    res.update(course_id=course_id, sections=sections, link=acct.link(f"course/view.php?id={course_id}"))
    return res


async def raw_modules(acct: Account, course_id: int) -> list[dict]:
    """Flat list of raw module dicts with section info (internal use)."""
    raw = await acct.client().call("core_course_get_contents", {"courseid": course_id})
    out = []
    for s in raw:
        for m in s.get("modules", []):
            m = dict(m)
            m["_section"] = s.get("section")
            m["_section_name"] = s.get("name")
            out.append(m)
    return out
