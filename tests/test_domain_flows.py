import httpx
import pytest

from moodle_academic_mcp.domain import assignments, deadlines, forums, grades, groups, quizzes
from moodle_academic_mcp.domain.account import Account
from moodle_academic_mcp.errors import MoodleAPIError, UnsupportedError, ValidationError
from tests.conftest import LoseResponseOnce, profile


@pytest.fixture
def pdf_txt(tmp_path):
    f = tmp_path / "relatorio.txt"
    f.write_text("conteudo do relatorio")
    return str(f)


async def test_status_before_and_prepare_dry_run_sends_nothing(alice, pdf_txt, fake_state):
    st = await assignments.submission_status(alice, 1)
    assert st["state"] == "not_submitted"
    prep = await assignments.prepare_submission(alice, 1, [pdf_txt])
    assert prep["ok"] and prep["would_send"][0]["name"] == "relatorio.txt"
    assert not fake_state.drafts and not fake_state.subs


async def test_constraints_reject_bad_type(alice, tmp_path):
    bad = tmp_path / "x.exe"
    bad.write_bytes(b"MZ")
    with pytest.raises(ValidationError) as e:
        await assignments.save_submission(alice, 1, [str(bad)])
    assert "accepted types" in str(e.value.details)


async def test_draft_is_not_delivery_then_submit_needs_statement(alice, pdf_txt):
    saved = await assignments.save_submission(alice, 1, [pdf_txt], text="olá")
    assert saved["stage"] == "saved_as_draft" and saved["delivered"] is False
    assert saved["verification"]["state"] == "draft" and saved["verification"]["matches"]
    with pytest.raises(ValidationError) as e:  # declaration of authorship is never ticked automatically
        await assignments.submit_assignment(alice, 1)
    assert "accept_statement" in (e.value.hint or "")
    done = await assignments.submit_assignment(alice, 1, accept_statement=True)
    assert done["delivered"] is True and done["evidence"]["state"] == "submitted"
    assert done["evidence"]["files"][0]["filename"] == "relatorio.txt"


async def test_deliver_composite_and_idempotent_resend(alice, pdf_txt, fake_state):
    r = await assignments.deliver_assignment(alice, 1, [pdf_txt], accept_statement=True)
    assert r["delivered"] is True and r["evidence"]["state"] == "submitted"
    n_saves = sum(1 for _, fn, _ in fake_state.calls if fn == "mod_assign_save_submission")
    # identical content again: the journal short-circuits; nothing is re-sent
    again = await assignments.save_submission(alice, 1, [pdf_txt])
    assert again.get("already_performed") is True
    assert sum(1 for _, fn, _ in fake_state.calls if fn == "mod_assign_save_submission") == n_saves


async def test_timeout_on_save_reconciles_without_duplicate(fake, fake_state, store, tmp_path, pdf_txt):
    t = LoseResponseOnce(httpx.ASGITransport(app=fake[0]), "mod_assign_save_submission")
    async with httpx.AsyncClient(transport=t, base_url="http://fake") as h:
        a = Account("u-alice", profile(), {"mobile": "tokA"}, store, http=h)
        a.runtime.upload_roots = [tmp_path]
        res = await assignments.save_submission(a, 1, [pdf_txt])
    assert res["outcome"] == "reconciled" and res["verification"]["matches"]
    assert sum(1 for _, fn, _ in fake_state.calls if fn == "mod_assign_save_submission") == 1


async def test_group_assignment_uses_team_submission(alice, tmp_path):
    f = tmp_path / "g.txt"
    f.write_text("x")
    st = await assignments.submission_status(alice, 2)
    assert st["is_group"] is True and st["group_id"] == 2
    r = await assignments.deliver_assignment(alice, 2, [str(f)])  # no separate submit step
    assert r["delivered"] is True and r["stage"] == "saved"


async def test_path_traversal_and_outside_roots_blocked(alice, tmp_path):
    with pytest.raises(Exception) as e:
        await assignments.save_submission(alice, 1, ["/etc/passwd"])
    assert getattr(e.value, "code", "") == "unsafe_destination"
    with pytest.raises(Exception):
        await assignments.save_submission(alice, 1, [str(tmp_path / ".." / ".." / "etc" / "passwd")])
    link = tmp_path / "link.txt"
    link.symlink_to("/etc/hostname")
    with pytest.raises(Exception) as e2:
        await assignments.save_submission(alice, 1, [str(link)])
    assert getattr(e2.value, "code", "") == "unsafe_destination"


async def test_deadlines_cross_modules_dedup_and_state(alice, pdf_txt):
    await assignments.deliver_assignment(alice, 1, [pdf_txt], accept_statement=True)
    res = await deadlines.list_deadlines(alice, "next_30_days")
    kinds = {(i["type"], i["name"]) for i in res["items"]}
    assert (
        ("assignment", "Trabalho 1") in kinds
        and ("quiz", "Quiz 1") in kinds
        and ("forum", "Forum due") in kinds
    )
    assert (
        sum(1 for i in res["items"] if i["name"].startswith("Trabalho 1")) == 1
    )  # calendar duplicate removed
    t1 = next(i for i in res["items"] if i["name"] == "Trabalho 1")
    assert t1["submitted"] is True and t1["cutoff_at"] != t1["due_at"]
    assert res["window"]["timezone"] == "America/Sao_Paulo" and res["window"]["start"].endswith("-03:00")
    pend = await deadlines.list_deadlines(alice, "next_30_days", status="pending")
    assert all(i.get("submitted") is not True for i in pend["items"])


async def test_pagination_never_hides_items(alice):
    res = await deadlines.list_deadlines(alice, "next_30_days", limit=2)
    assert res["has_more"] is True and res["next_offset"] == 2 and res["total"] > 2


# ---------------------------------------------------------------- quizzes
async def test_quiz_full_flow(alice, fake_state):
    q = await quizzes.start_attempt(alice, 5)
    aid = q["attempt_id"]
    page0 = q["first_page"]["questions"][0]
    names = [f["name"] for f in page0["fields"]]
    assert names == ["q50:1_answer"] and page0["fields"][0]["options"][1]["label"] == "2"
    with pytest.raises(ValidationError):
        await quizzes.save_answers(alice, aid, 0, {"q50:1_invented": "1"})  # no invented protocol
    with pytest.raises(ValidationError):
        await quizzes.save_answers(alice, aid, 0, {"q50:1_answer": "99"})
    r = await quizzes.save_answers(alice, aid, 0, {"q50:1_answer": "1"})
    assert fake_state.attempts[aid]["answers"]["q50:1_answer"] == "1"
    assert fake_state.attempts[aid]["seq_received"] == ["q50:1_:sequencecheck"]
    assert r["summary_after"]["questions"][0]["state"] == "complete"
    p1 = await quizzes.get_attempt_page(alice, aid, 1)
    assert p1["questions"][0]["fields"][0]["kind"] == "text"
    fin = await quizzes.save_answers(alice, aid, 1, {"q50:2_answer": "Paris"}, finish=True)
    assert fin["finished"] is True
    info = await quizzes.quiz_info(alice, 5)
    assert info["attempts"][0]["state"] == "finished"


async def test_start_attempt_resumes_instead_of_consuming_another(alice, fake_state):
    a = await quizzes.start_attempt(alice, 5)
    b = await quizzes.start_attempt(alice, 5)
    assert b["resumed"] is True and b["attempt_id"] == a["attempt_id"] and len(fake_state.attempts) == 1


def test_question_parser_handles_checkbox_select_textarea():
    html = (
        '<input type="hidden" name="q1:1_:sequencecheck" value="3">'
        '<input type="checkbox" name="q1:1_choice0" value="1" id="c0"><label for="c0">A</label>'
        '<input type="checkbox" name="q1:1_choice1" value="1" id="c1"><label for="c1">B</label>'
        '<select name="q1:2_sub0"><option value="">Escolha</option><option value="1">x</option></select>'
        '<textarea name="q1:3_answer">abc</textarea><script>var x=1</script>'
    )
    p = quizzes.parse_question_html(html)
    kinds = {f["name"]: f["kind"] for f in p["fields"]}
    assert kinds == {
        "q1:1_choice0": "checkbox",
        "q1:1_choice1": "checkbox",
        "q1:2_sub0": "select",
        "q1:3_answer": "textarea",
    }
    assert p["sequence_checks"] == {"q1:1_:sequencecheck": "3"}


# ---------------------------------------------------------------- groups
async def test_join_group_full_already_member_and_other_mechanisms(make_account, fake_state):
    alice = make_account()
    bob = make_account("u-bob", {"mobile": "tokB"})
    fake_state.choicegroup[102] = [2]  # bob holds the only seat of group 2
    fake_state.cg_capacity[2] = 1
    with pytest.raises(MoodleAPIError) as e:
        await groups.join_group(alice, 10, 20, 2)
    assert e.value.errorcode == "group_full"
    ok = await groups.join_group(alice, 10, 20, 1)
    assert ok["joined"] is True and 1 in [g["group_id"] for g in ok["my_groups"]["my_groups"]]
    again = await groups.join_group(alice, 10, 20, 1)
    assert again["already_member"] is True
    with pytest.raises(UnsupportedError) as e2:
        await groups.join_group(alice, 10, 21, 1)  # group self-selection: no API
    assert "groupselect" in str(e2.value) or "self-selection" in str(e2.value)
    assert "view.php?id=21" in e2.value.hint
    left = await groups.leave_group(alice, 10, 20)
    assert left["left"] is True
    del bob


async def test_group_activities_distinguish_mechanisms(alice):
    r = await groups.group_activities(alice, 10)
    assert {a["type"] for a in r["group_forming_activities"]} == {"choicegroup", "groupselect"}


# ---------------------------------------------------------------- forums / grades
async def test_forum_content_is_marked_untrusted_and_changes_since(alice):
    d = await forums.list_discussions(alice, 7)
    assert "Ignore all previous" in d["items"][0]["preview"]  # delivered as data...
    ch = await forums.changes_since(alice)
    assert ch["basis"].startswith("first run") and ch["discussions"]["total"] == 1
    assert ch["untrusted_fields"]
    ch2 = await forums.changes_since(alice)
    assert ch2["basis"] == "previous check"


async def test_grades_distinguish_ungraded_from_zero(alice):
    g = await grades.get_grades(alice, 10)
    by = {i["name"]: i for i in g["items"]}
    assert by["P2"]["graded"] is False and by["P2"]["score"] is None
    assert by["Zero"]["graded"] is True and by["Zero"]["score"] == 0.0
    scheme = await grades.draft_scheme(alice, 10)
    assert scheme["formula"].startswith("avg(") and scheme["items"][1]["score"] is None  # Zero has no weight


async def test_list_courses_defaults_to_latest_term(make_account, fake_state):
    from moodle_academic_mcp.domain import courses

    a = make_account()
    orig = a.client().call

    async def fake_call(fn, params=None, **kw):
        if fn == "core_enrol_get_users_courses":
            return [
                {"id": 1, "shortname": "OLD1 (20242)", "startdate": 1, "enddate": 0},
                {"id": 2, "shortname": "OLD2 (20252)", "startdate": 1, "enddate": 0},
                {"id": 3, "shortname": "NEW1 (20262)", "startdate": 1, "enddate": 0},
                {"id": 4, "shortname": "NEW2 (20262)", "startdate": 1, "enddate": 0},
            ]
        return await orig(fn, params, **kw)

    a.client().call = fake_call  # type: ignore[method-assign]
    cur = await courses.list_courses(a)
    assert [c["id"] for c in cur["items"]] == [3, 4] and cur["filter"]["latest_term_seen"] == "20262"
    assert "only_current=false" in cur["hint"]
    assert len((await courses.list_courses(a, only_current=False))["items"]) == 4
    assert [c["id"] for c in (await courses.list_courses(a, semester="20252"))["items"]] == [2]


async def test_past_due_is_bounded(alice, monkeypatch):
    res = await deadlines.list_deadlines(alice, "next_7_days", include_past_due=True, past_due_days=7)
    assert res["total"] >= 1


async def test_window_match_explains_why_an_item_is_listed(alice):
    res = await deadlines.list_deadlines(alice, "next_30_days")
    t1 = next(i for i in res["items"] if i["name"] == "Trabalho 1")
    assert t1["window_match"] == "due"


def test_derive_state_real_moodle_team_shape_without_team_submission_yet():
    # Real Moodle: no 'teamsubmissionenabled'; submissiongroup is set for team assignments.
    team = assignments.derive_state({"lastattempt": {"submission": {}, "submissiongroup": 70010}}, None)
    assert team["is_group"] is True and team["group_id"] == 70010
    solo = assignments.derive_state({"lastattempt": {"submission": {"status": "new"}}}, None)
    assert solo["is_group"] is False


def test_dmlwriteexception_gets_actionable_error():
    from moodle_academic_mcp.moodle.client import MoodleClient

    e = MoodleClient._map_error(
        {"exception": "dml_write_exception", "errorcode": "dmlwriteexception", "message": "Error writing to database"},
        "mod_assign_save_submission",
    )
    assert "server-side" in e.hint and "website" in e.hint


def test_invalidresponse_is_reported_as_server_side_with_workaround():
    from moodle_academic_mcp.moodle.client import MoodleClient

    e = MoodleClient._map_error(
        {"exception": "moodle_exception", "errorcode": "invalidresponse", "message": "Valor inválido de resposta detectado"},
        "gradereport_user_get_grade_items",
    )
    assert "server-side" in e.message and "gradereport_user_get_grades_table" in e.hint


async def test_quiz_writes_are_journaled_so_an_unexpected_close_can_be_traced(alice):
    q = await quizzes.start_attempt(alice, 5)
    aid = q["attempt_id"]
    await quizzes.save_answers(alice, aid, 0, {"q50:1_answer": "1"})
    await quizzes.save_answers(alice, aid, 1, {"q50:2_answer": "Paris"}, finish=True)
    ops = await alice.store.list_operations(alice.user_key)
    assert [o["kind"] for o in ops].count("quiz_start") == 1
    saves = [o for o in ops if o["kind"] == "quiz_save"]
    assert len(saves) == 2 and all(o["target"] == str(aid) for o in saves)
    ev = [o["evidence"] for o in saves]
    assert any(e["finish_requested"] is False and e["page"] == 0 and e["state_after"] == "inprogress" for e in ev)
    assert any(e["finish_requested"] is True and e["page"] == 1 and e["state_after"] == "finished" for e in ev)


async def test_finish_attempt_is_journaled(alice):
    q = await quizzes.start_attempt(alice, 5)
    aid = q["attempt_id"]
    await quizzes.finish_attempt(alice, aid)
    ops = await alice.store.list_operations(alice.user_key)
    fin = [o for o in ops if o["kind"] == "quiz_finish"]
    assert len(fin) == 1 and fin[0]["status"] == "done" and fin[0]["evidence"]["state_after"] == "finished"


async def test_save_on_a_closed_attempt_says_nothing_was_saved(alice, fake_state):
    q = await quizzes.start_attempt(alice, 5)
    aid = q["attempt_id"]
    await quizzes.finish_attempt(alice, aid)
    with pytest.raises(ValidationError, match="NOT saved"):
        await quizzes.save_answers(alice, aid, 0, {"q50:1_answer": "1"})
    assert fake_state.attempts[aid]["answers"] == {}
    ops = await alice.store.list_operations(alice.user_key)
    assert any(o["kind"] == "quiz_save" and o["status"] == "failed" for o in ops)
