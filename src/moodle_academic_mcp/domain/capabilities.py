"""Feature x state matrix for one account (implemented / documented / detected / validated / unavailable)."""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

from ..moodle.catalog import State
from .account import Account

# feature id -> (title, functions (ALL needed), preferred service, provenance of the schema)
FEATURES: dict[str, tuple[str, list[str], str, str]] = {
    "courses": ("List courses", ["core_enrol_get_users_courses"], "mobile", "ufsc_doc"),
    "course_contents": ("Course structure and materials", ["core_course_get_contents"], "mobile", "ufsc_doc"),
    "assignments": (
        "Assignment statements and status",
        ["mod_assign_get_assignments", "mod_assign_get_submission_status"],
        "mobile",
        "ufsc_doc",
    ),
    "assignment_delivery": (
        "Save and submit assignments",
        ["mod_assign_save_submission", "mod_assign_submit_for_grading"],
        "mobile",
        "ufsc_doc",
    ),
    "file_upload": ("Draft-area upload (webservice/upload.php)", [], "mobile", "moodle_docs"),
    "calendar": (
        "Calendar action events",
        ["core_calendar_get_action_events_by_timesort"],
        "mobile",
        "ufsc_doc",
    ),
    "quizzes": (
        "Quiz listing and attempts",
        ["mod_quiz_get_quizzes_by_courses", "mod_quiz_get_user_attempts"],
        "mobile",
        "ufsc_doc",
    ),
    "quiz_attempts": (
        "Start/answer/finish quiz attempts",
        ["mod_quiz_start_attempt", "mod_quiz_get_attempt_data", "mod_quiz_process_attempt"],
        "mobile",
        "ufsc_doc",
    ),
    "forums": (
        "Forums and announcements",
        [
            "mod_forum_get_forums_by_courses",
            "mod_forum_get_forum_discussions",
            "mod_forum_get_discussion_posts",
        ],
        "mobile",
        "ufsc_doc",
    ),
    "forum_posting": (
        "Post/reply in forums",
        ["mod_forum_add_discussion", "mod_forum_add_discussion_post"],
        "mobile",
        "ufsc_doc",
    ),
    "grades": ("Gradebook items", ["gradereport_user_get_grade_items"], "mobile", "ufsc_doc"),
    "core_groups": (
        "Core groups / groupings / allowed groups",
        [
            "core_group_get_course_user_groups",
            "core_group_get_course_groups",
            "core_group_get_activity_allowed_groups",
        ],
        "mobile",
        "ufsc_doc",
    ),
    "choice": (
        "Choice (mod_choice) polls",
        ["mod_choice_get_choice_options", "mod_choice_submit_choice_response"],
        "mobile",
        "ufsc_doc",
    ),
    "group_choice": (
        "Group choice (mod_choicegroup) join/leave",
        [
            "mod_choicegroup_get_choicegroup_options",
            "mod_choicegroup_submit_choicegroup_response",
            "mod_choicegroup_delete_choicegroup_responses",
        ],
        "mobile",
        "upstream",
    ),
    "group_self_selection": ("Group self-selection (mod_groupselect)", [], "mobile", "none: no WS upstream"),
    "attendance": (
        "Attendance sessions/records",
        ["mod_attendance_get_sessions", "mod_attendance_get_session"],
        "attendance",
        "upstream",
    ),
    "vpl": (
        "VPL read/submit/evaluate",
        ["mod_vpl_info", "mod_vpl_open", "mod_vpl_save", "mod_vpl_evaluate"],
        "vpl",
        "upstream",
    ),
    "materials": ("Download/extract materials", ["core_course_get_contents"], "mobile", "ufsc_doc"),
}
ALWAYS_LOCAL = {"attendance_math", "grade_math", "materials_extraction", "manual_attendance_import"}


def _validation() -> dict:
    return json.loads(
        resources.files("moodle_academic_mcp.data").joinpath("validation.json").read_text("utf-8")
    )


async def matrix(acct: Account, detect: bool = True) -> dict[str, Any]:
    val = _validation()
    detected: dict[str, set[str] | None] = {}
    errors: dict[str, str] = {}
    if detect:
        from ..errors import MamcpError

        for s in acct.services():
            try:
                info = await acct.site_info(s)
                detected[s] = {f["name"] for f in info.get("functions", [])}
            except MamcpError as e:
                detected[s] = None
                errors[s] = e.code
    rows = {}
    for fid, (title, funcs, pref, prov) in FEATURES.items():
        states = [State.IMPLEMENTED.value]
        documented = (
            all(f in acct.catalog.functions for f in funcs) if funcs else prov != "none: no WS upstream"
        )
        if documented:
            states.append(State.DOCUMENTED.value)
        where = [s for s, d in detected.items() if d is not None and funcs and all(f in d for f in funcs)]
        if where:
            states.append(State.DETECTED.value)
        elif (
            detect
            and funcs
            and any(d is not None for d in detected.values())
            and not any(d is None for d in detected.values())
        ):
            states.append(State.UNAVAILABLE.value)
        else:
            states.append(State.UNKNOWN.value) if funcs and detect else None
        if fid in val.get("validated_sandbox", {}):
            states.append(State.VALIDATED_SANDBOX.value)
        if fid in val.get("validated_ufsc", {}):
            states.append(State.VALIDATED_UFSC.value)
        if fid == "group_self_selection":
            states = [State.UNAVAILABLE.value]
        rows[fid] = {
            "title": title,
            "states": states,
            "schema_source": prov,
            "detected_in_services": where,
            "preferred_service": pref,
        }
    return {
        "profile": acct.profile.name,
        "services_configured": acct.services(),
        "detection_errors": errors,
        "features": rows,
        "local_only_features": sorted(ALWAYS_LOCAL),
        "legend": {s.value: s.name for s in State},
        "note": "Mock/fake-server tests are never reported as validated_*; see data/validation.json.",
    }
