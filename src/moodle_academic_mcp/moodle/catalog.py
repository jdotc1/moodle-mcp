"""Function catalogs: provenance, parameter shapes and read/write classification.

Sources (every entry records where it came from):
  * `ufsc_mobile`  - bundled, generated from the UFSC wsdoc export of the *mobile* service (2026-11-03)
  * `upstream_*`   - hand-written from upstream plugin source (Attendance, VPL, Group choice); NOT from UFSC
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from importlib import resources
from typing import Any

from .params import flatten


class Effect(StrEnum):
    READ = "read"  # no side effect
    VIEW = "view"  # logs a view / completion; harmless but not read-only
    WRITE = "write"  # changes state or consumes resources


class State(StrEnum):
    IMPLEMENTED = "implemented"
    DOCUMENTED = "documented"
    DETECTED = "detected_in_service"
    VALIDATED_SANDBOX = "validated_sandbox"
    VALIDATED_UFSC = "validated_ufsc"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


# Functions that are named like reads but have effects or return secrets.
_FORCE_WRITE = {
    "core_files_get_unused_draft_itemid",
    "mod_vpl_get_result",
    "mod_forum_prepare_draft_area_for_post",
}
_SECRET_OUTPUT = {"core_calendar_get_calendar_export_token", "core_user_get_user_preferences_secret"}
_DESTRUCTIVE = re.compile(r"(^|_)(delete|remove|block|revert|unenrol|reset|purge)(_|$)")
_READ_PREFIX = re.compile(r"_(get|list|search|fetch|check|is|can)_")
_VIEW_PREFIX = re.compile(r"_view_")


def classify(name: str) -> Effect:
    if name in _FORCE_WRITE:
        return Effect.WRITE
    if _VIEW_PREFIX.search(name):
        return Effect.VIEW
    if _READ_PREFIX.search(name) and not _DESTRUCTIVE.search(name):
        return Effect.READ
    return Effect.WRITE


@dataclass
class FunctionInfo:
    name: str
    description: str
    params: list[dict] | None  # None = schema not verified
    source: str
    effect: Effect
    destructive: bool = False
    sensitive_output: bool = False
    notes: str = ""

    def top_level(self) -> dict[str, bool]:
        out: dict[str, bool] = {}
        for p in self.params or []:
            out[p["top"]] = out.get(p["top"], False) or bool(p.get("required"))
        return out

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "effect": self.effect.value,
            "destructive": self.destructive,
            "source": self.source,
            "params": [
                {"name": p["name"], "type": p["type"], "required": p["required"]} for p in (self.params or [])
            ]
            if self.params is not None
            else "unverified",
            "notes": self.notes,
        }


def _p(name: str, typ: str = "int", required: bool = True) -> dict:
    return {"name": name, "type": typ, "top": re.split(r"\[", name, maxsplit=1)[0], "required": required}


# --- adapter catalogs written from upstream plugin source -------------------------------------------------
_UPSTREAM: dict[str, list[tuple[str, str, Effect, list[dict], str]]] = {
    "upstream_mod_attendance@MOODLE_405_STABLE (danmarsden/moodle-mod_attendance db/services.php, externallib.php)": [
        (
            "mod_attendance_get_sessions",
            "Sessions of an attendance instance.",
            Effect.READ,
            [_p("attendanceid")],
            "Per-student log visibility depends on capabilities; unverified for students.",
        ),
        (
            "mod_attendance_get_session",
            "One session with statuses and attendance_log.",
            Effect.READ,
            [_p("sessionid")],
            "",
        ),
        (
            "mod_attendance_get_courses_with_today_sessions",
            "Teacher: courses with sessions today.",
            Effect.READ,
            [_p("userid")],
            "Teacher-oriented.",
        ),
        (
            "mod_attendance_update_user_status",
            "Take attendance (teacher).",
            Effect.WRITE,
            [_p("sessionid"), _p("studentid"), _p("takenbyid"), _p("statusid"), _p("statusset", "string")],
            "Teacher capability; never used by the student flows.",
        ),
    ],
    "upstream_mod_vpl@master (jcrodriguez-dis/moodle-mod_vpl externallib.php)": [
        (
            "mod_vpl_info",
            "VPL activity description and required files.",
            Effect.READ,
            [_p("id"), _p("password", "string", False)],
            "",
        ),
        (
            "mod_vpl_open",
            "Files of the last submission.",
            Effect.READ,
            [_p("id"), _p("password", "string", False), _p("userid", "int", False)],
            "",
        ),
        (
            "mod_vpl_save",
            "Save/submit files (consumes a submission).",
            Effect.WRITE,
            [
                _p("id"),
                _p("files[0][name]", "string"),
                _p("files[0][data]", "string"),
                _p("files[0][encoding]", "int", False),
                _p("password", "string", False),
                _p("userid", "int", False),
                _p("comments", "string", False),
            ],
            "",
        ),
        (
            "mod_vpl_evaluate",
            "Start remote evaluation (consumes server resources).",
            Effect.WRITE,
            [_p("id"), _p("password", "string", False), _p("userid", "int", False)],
            "",
        ),
        (
            "mod_vpl_get_result",
            "Result of the last evaluation.",
            Effect.WRITE,
            [_p("id"), _p("password", "string", False), _p("userid", "int", False)],
            "Declared type 'write' upstream; treated as a write here.",
        ),
    ],
    "upstream_mod_choicegroup@master (ndunand/moodle-mod_choicegroup classes/external.php)": [
        (
            "mod_choicegroup_get_choicegroup_options",
            "Options (groups), counts, limits, own choice.",
            Effect.READ,
            [_p("choicegroupid"), _p("userid")],
            "userid must be the caller.",
        ),
        (
            "mod_choicegroup_submit_choicegroup_response",
            "Join the chosen group(s).",
            Effect.WRITE,
            [
                _p("choicegroupid"),
                _p("data[0][name]", "string", False),
                _p("data[0][value]", "string", False),
            ],
            "Single choice: name='responses', value=<option id>. Multiple: name='responses_<id>', value='true'.",
        ),
        (
            "mod_choicegroup_delete_choicegroup_responses",
            "Leave the chosen group(s).",
            Effect.WRITE,
            [_p("choicegroupid")],
            "Allowed only if the activity allows updating the choice.",
        ),
        ("mod_choicegroup_view_choicegroup", "Log a view.", Effect.VIEW, [_p("choicegroupid")], ""),
    ],
}


class Catalog:
    def __init__(self) -> None:
        self.functions: dict[str, FunctionInfo] = {}
        self.provenance: dict[str, dict] = {}

    def add_wsdoc(self, doc: dict, source_id: str) -> None:
        self.provenance[source_id] = doc.get("provenance", {})
        for name, f in doc["functions"].items():
            eff = classify(name)
            self.functions[name] = FunctionInfo(
                name=name,
                description=f["description"],
                params=f["params"],
                source=source_id,
                effect=eff,
                destructive=bool(_DESTRUCTIVE.search(name)),
                sensitive_output=name in _SECRET_OUTPUT,
            )

    def add_upstream(self) -> None:
        for source, items in _UPSTREAM.items():
            self.provenance[source] = {"note": "From upstream source code, not from UFSC documentation."}
            for name, desc, eff, params, notes in items:
                self.functions.setdefault(
                    name,
                    FunctionInfo(
                        name, desc, params, source, eff, bool(_DESTRUCTIVE.search(name)), False, notes
                    ),
                )

    def get(self, name: str) -> FunctionInfo | None:
        return self.functions.get(name)

    def search(self, query: str, limit: int = 25) -> list[FunctionInfo]:
        terms = [t for t in re.split(r"\W+", query.lower()) if t]
        scored = []
        for f in self.functions.values():
            hay = (f.name + " " + f.description).lower()
            score = sum(hay.count(t) for t in terms)
            if score and all(t in hay for t in terms):
                scored.append((score, f))
        scored.sort(key=lambda x: (-x[0], x[1].name))
        return [f for _, f in scored[:limit]]

    def validate(self, name: str, params: dict[str, Any] | None) -> list[str]:
        """Problems with the call against the *verified* schema. Empty list == acceptable."""
        info = self.get(name)
        if info is None:
            return [f"{name} is not in any bundled catalog; it cannot be invoked through the generic tool."]
        if info.params is None:
            return [f"{name} has no verified parameter schema."]
        known = info.top_level()
        problems = []
        given = {k.split("[", 1)[0] for k, _ in flatten(params)}
        for k in sorted(given - set(known)):
            problems.append(f"unknown parameter {k!r}")
        for k, req in known.items():
            if req and k not in given:
                problems.append(f"missing required parameter {k!r}")
        return problems


@lru_cache(maxsize=1)
def load_catalog() -> Catalog:
    cat = Catalog()
    raw = resources.files("moodle_academic_mcp.data").joinpath("catalog_ufsc_mobile.json").read_text("utf-8")
    cat.add_wsdoc(json.loads(raw), "ufsc_mobile")
    cat.add_upstream()
    return cat
