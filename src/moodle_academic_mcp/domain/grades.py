"""Gradebook reading. 'Not graded' (None) is never conflated with zero."""

from __future__ import annotations

import re
from typing import Any

from ..calc import grades as calc
from .account import Account


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


def _pct(v: Any) -> float | None:
    if not isinstance(v, str):
        return _num(v)
    m = re.search(r"-?\d+(?:[.,]\d+)?", v)
    return float(m.group().replace(",", ".")) if m else None


async def get_grades(acct: Account, course_id: int) -> dict[str, Any]:
    uid = await acct.userid()
    res = await acct.client().call("gradereport_user_get_grade_items", {"courseid": course_id, "userid": uid})
    ug = (res.get("usergrades") or [{}])[0]
    items = []
    for g in ug.get("gradeitems", []):
        raw = g.get("graderaw")
        formatted = (g.get("gradeformatted") or "").strip()
        graded = raw is not None and formatted not in ("", "-")
        items.append(
            {
                "item_id": g.get("id"),
                "name": g.get("itemname")
                or ("Course total" if g.get("itemtype") == "course" else g.get("itemtype")),
                "type": g.get("itemtype"),
                "module": g.get("itemmodule"),
                "cmid": g.get("cmid"),
                "graded": graded,
                "score": _num(raw) if graded else None,
                "display": formatted or None,
                "min": _num(g.get("grademin")),
                "max": _num(g.get("grademax")),
                "weight_percent": _pct(g.get("weightformatted"))
                if g.get("weightformatted")
                else _num(g.get("weightraw")),
                "percentage": g.get("percentageformatted") or None,
                "category_id": g.get("categoryid"),
                "hidden": bool(g.get("hidden")),
                "feedback": re.sub(r"<[^>]+>", "", g.get("feedback") or "").strip() or None,
            }
        )
    return {
        "course_id": course_id,
        "items": items,
        "total_items": len(items),
        "ungraded_items": [i["name"] for i in items if not i["graded"] and i["type"] != "category"],
        "note": "graded=false means no grade released yet (not zero). Gradebook weights are the course's own "
        "setup and may differ from the official syllabus rule.",
    }


async def draft_scheme(acct: Account, course_id: int) -> dict[str, Any]:
    """Starter scheme from gradebook items (scores, maxima, weights) for simulate/required_score.
    The *rule* (formula) comes from the syllabus; it is only a gradebook-based default here."""
    g = await get_grades(acct, course_id)
    items = []
    for i in g["items"]:
        if i["type"] in ("course", "category") or i["max"] is None:
            continue
        name = re.sub(r"\W+", "_", i["name"]).strip("_") or f"item{i['item_id']}"
        if name[0].isdigit():
            name = "_" + name
        items.append(
            {
                "name": name,
                "score": i["score"],
                "max": i["max"],
                "weight": i["weight_percent"],
                "label": i["name"],
            }
        )
    return {
        "source": "gradebook of the course (not the syllabus): confirm against the plano de ensino",
        "formula": "weighted"
        if items and all(i["weight"] is not None for i in items)
        else "avg(" + ", ".join(i["name"] for i in items) + ")"
        if items
        else "0",
        "items": items,
        "pass_mark": None,
    }


def simulate(scheme: dict, overrides: dict[str, Any] | None = None) -> dict:
    return calc.compute(scheme, overrides)


def required_score(
    scheme: dict, item: str, target: Any = None, fill_others: str | None = None, step: str = "0.01"
) -> dict:
    return calc.required_score(scheme, item, target, step=step, fill_others=fill_others)
