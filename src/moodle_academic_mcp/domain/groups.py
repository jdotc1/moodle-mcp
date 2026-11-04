"""Groups: core groups, group-choice plugins and the honest limits of each mechanism."""

from __future__ import annotations

from typing import Any

from ..errors import MamcpError, MoodleAPIError, NotFoundError, UnsupportedError, ValidationError
from .account import Account
from .courses import raw_modules

GROUP_MODS = {
    "choicegroup": "Group choice (mod_choicegroup): members choose a group. API: mod_choicegroup_* (plugin).",
    "groupselect": "Group self-selection (mod_groupselect): no web service API exists upstream.",
    "choice": "Choice (mod_choice): a poll. Answering it does NOT by itself put you in a group.",
}


async def my_groups(acct: Account, course_id: int) -> dict[str, Any]:
    uid = await acct.userid()
    c = acct.client()
    res = await c.call("core_group_get_course_user_groups", {"courseid": course_id, "userid": uid})
    groupings: list[dict[str, Any]] = []
    try:
        groupings = await c.call("core_group_get_course_groupings", {"courseid": course_id}) or []
    except MamcpError:
        pass
    return {
        "course_id": course_id,
        "my_groups": [
            {"group_id": g["id"], "name": g["name"], "description": g.get("description")}
            for g in res.get("groups", [])
        ],
        "groupings": [{"grouping_id": g["id"], "name": g["name"]} for g in groupings],
    }


async def course_groups(acct: Account, course_id: int, with_members: bool = False) -> dict[str, Any]:
    groups = await acct.client().call("core_group_get_course_groups", {"courseid": course_id}) or []
    out = []
    for g in groups:
        item: dict[str, Any] = {
            "group_id": g["id"],
            "name": g["name"],
            "description": g.get("description"),
            "enrol_key_required": bool(g.get("enrolmentkey")),
        }
        if with_members:
            try:
                users = await acct.client().call(
                    "core_enrol_get_enrolled_users",
                    {
                        "courseid": course_id,
                        "options": [
                            {"name": "groupid", "value": g["id"]},
                            {"name": "onlyactive", "value": 1},
                        ],
                    },
                )
                item["members"] = [{"user_id": u["id"], "name": u.get("fullname")} for u in users]
            except MamcpError as e:
                item["members_error"] = e.to_dict()
        out.append(item)
    return {"course_id": course_id, "groups": out, "total": len(out)}


async def activity_groups(acct: Account, cmid: int) -> dict[str, Any]:
    uid = await acct.userid()
    c = acct.client()
    mode = await c.call("core_group_get_activity_groupmode", {"cmid": cmid})
    allowed = await c.call("core_group_get_activity_allowed_groups", {"cmid": cmid, "userid": uid})
    return {
        "cmid": cmid,
        "group_mode": {0: "none", 1: "separate", 2: "visible"}.get((mode or {}).get("groupmode", -1)),
        "allowed_groups": [
            {"group_id": g["id"], "name": g["name"]} for g in (allowed or {}).get("groups", [])
        ],
    }


async def group_activities(acct: Account, course_id: int) -> dict[str, Any]:
    """Which mechanisms this course uses to form groups (so the right adapter is picked)."""
    mods = await raw_modules(acct, course_id)
    found = [
        {
            "cmid": m["id"],
            "instance": m.get("instance"),
            "type": m["modname"],
            "name": m["name"],
            "mechanism": GROUP_MODS[m["modname"]],
            "link": m.get("url"),
        }
        for m in mods
        if m["modname"] in GROUP_MODS
    ]
    teams = [
        {
            "cmid": m["id"],
            "name": m["name"],
            "type": "assign",
            "note": "check team_submission in assignment settings",
        }
        for m in mods
        if m["modname"] == "assign" and "grupo" in (m.get("name") or "").lower()
    ]
    return {"course_id": course_id, "group_forming_activities": found, "possible_group_assignments": teams}


async def _choicegroup_options(acct: Account, instance: int) -> dict[str, Any]:
    uid = await acct.userid()
    return await acct.client().call(
        "mod_choicegroup_get_choicegroup_options", {"choicegroupid": instance, "userid": uid}
    )


async def choicegroup_options(acct: Account, course_id: int, cmid: int) -> dict[str, Any]:
    mod = await _find_mod(acct, course_id, cmid, "choicegroup")
    try:
        res = await _choicegroup_options(acct, mod["instance"])
    except MamcpError as e:
        if e.code in ("function_unavailable", "permission_denied"):
            raise UnsupportedError(
                "The Group choice web service functions are not enabled for your token.",
                hint=f"Join through the browser: {mod.get('url')}. (Needs mod_choicegroup web services enabled in "
                "the external service.)",
            ) from e
        raise
    opts = [
        {
            "option_id": o["id"],
            "group_id": o.get("groupid"),
            "name": o["name"],
            "taken": o.get("countanswers"),
            "capacity": o.get("maxanswers") or None,
            "full": bool(o.get("maxanswers")) and (o.get("countanswers") or 0) >= o["maxanswers"],
            "mine": bool(o.get("checked")),
            "disabled": bool(o.get("disabled")),
        }
        for o in res.get("options", [])
    ]
    return {"cmid": cmid, "choicegroup_id": mod["instance"], "options": opts, "link": mod.get("url")}


async def _find_mod(acct: Account, course_id: int, cmid: int, modname: str) -> dict:
    for m in await raw_modules(acct, course_id):
        if m["id"] == cmid:
            if m["modname"] != modname:
                raise UnsupportedError(
                    f"Activity {cmid} is a '{m['modname']}', not '{modname}'.",
                    hint=GROUP_MODS.get(m["modname"], "Different mechanism: see group_activities."),
                )
            return m
    raise NotFoundError(f"Activity {cmid} not found in course {course_id}.")


async def join_group(acct: Account, course_id: int, cmid: int, group_id: int) -> dict[str, Any]:
    """WRITE: join `group_id` through the course's Group-choice activity. Refuses full/closed groups."""
    mods = {m["id"]: m for m in await raw_modules(acct, course_id)}
    m = mods.get(cmid)
    if m is None:
        raise NotFoundError(f"Activity {cmid} not found in course {course_id}.")
    if m["modname"] == "groupselect":
        raise UnsupportedError(
            "Group self-selection (mod_groupselect) has no web service API; it cannot be automated here.",
            hint=f"Join manually: {m.get('url')}",
        )
    if m["modname"] == "choice":
        raise UnsupportedError(
            "This is a plain Choice poll: answering it records a vote, it does not join a group. Use "
            "choice_respond if a vote is what you want.",
            hint=m.get("url"),
        )
    if m["modname"] != "choicegroup":
        raise UnsupportedError(f"Activity type {m['modname']!r} is not a group-joining mechanism.")
    info = await choicegroup_options(acct, course_id, cmid)
    opt = next((o for o in info["options"] if o["group_id"] == group_id or o["option_id"] == group_id), None)
    if opt is None:
        raise NotFoundError(
            f"Group {group_id} is not an option of this activity.", details={"options": info["options"]}
        )
    if opt["mine"]:
        return {"joined": True, "already_member": True, "group": opt}
    if opt["full"]:
        raise MoodleAPIError(
            f"Group '{opt['name']}' is full ({opt['taken']}/{opt['capacity']}).", errorcode="group_full"
        )
    multiple = any(o["mine"] for o in info["options"])
    data = [{"name": "responses", "value": str(opt["option_id"])}]
    if multiple:
        data = [
            {"name": f"responses_{o['option_id']}", "value": "true"}
            for o in info["options"]
            if o["mine"] or o["option_id"] == opt["option_id"]
        ]
    await acct.client().call(
        "mod_choicegroup_submit_choicegroup_response",
        {"choicegroupid": info["choicegroup_id"], "data": data},
        write=True,
    )
    after = await choicegroup_options(acct, course_id, cmid)
    mine = next((o for o in after["options"] if o["option_id"] == opt["option_id"]), None)
    return {
        "joined": bool(mine and mine["mine"]),
        "group": mine,
        "my_groups": await my_groups(acct, course_id),
        "note": "Verified by reading the options back.",
    }


async def leave_group(acct: Account, course_id: int, cmid: int) -> dict[str, Any]:
    """WRITE: remove your response(s) in a Group-choice activity (only if the activity allows changing it)."""
    mod = await _find_mod(acct, course_id, cmid, "choicegroup")
    await acct.client().call(
        "mod_choicegroup_delete_choicegroup_responses", {"choicegroupid": mod["instance"]}, write=True
    )
    after = await choicegroup_options(acct, course_id, cmid)
    return {"left": not any(o["mine"] for o in after["options"]), "options": after["options"]}


# ---- plain Choice (mod_choice) ----------------------------------------------------------------------
async def choice_options(acct: Account, course_id: int, cmid: int) -> dict[str, Any]:
    mod = await _find_mod(acct, course_id, cmid, "choice")
    res = await acct.client().call("mod_choice_get_choice_options", {"choiceid": mod["instance"]})
    return {
        "cmid": cmid,
        "choice_id": mod["instance"],
        "name": mod["name"],
        "options": [
            {
                "option_id": o["id"],
                "text": o.get("text"),
                "checked": bool(o.get("checked")),
                "disabled": bool(o.get("disabled")),
                "max": o.get("maxanswers") or None,
                "taken": o.get("countanswers"),
            }
            for o in res.get("options", [])
        ],
        "note": "Answering a Choice records a vote only; it is not group membership.",
    }


async def choice_respond(acct: Account, course_id: int, cmid: int, option_ids: list[int]) -> dict[str, Any]:
    """WRITE: submit your answer(s) in a Choice poll."""
    if not option_ids:
        raise ValidationError("option_ids is empty")
    mod = await _find_mod(acct, course_id, cmid, "choice")
    await acct.client().call(
        "mod_choice_submit_choice_response",
        {"choiceid": mod["instance"], "responses": option_ids},
        write=True,
    )
    return await choice_options(acct, course_id, cmid)
