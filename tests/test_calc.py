from datetime import date, datetime
from fractions import Fraction as F
from zoneinfo import ZoneInfo

import pytest

from moodle_academic_mcp.calc import grades as g
from moodle_academic_mcp.calc.attendance import Policy, Session, analyze, parse_min_frequency
from moodle_academic_mcp.timeutil import window

TZ = ZoneInfo("America/Sao_Paulo")


def sess(prefix, n, w, cat):
    return [Session(f"{prefix}{i}", date(2026, 3, 1 + i), F(w), cat) for i in range(n)]


def test_spec_example_72h_75pct_10h_absent():
    # 10 absence hours (5 sessions x 2h), the rest still to come or attended
    ss = sess("a", 5, 2, "absent") + sess("p", 15, 2, "present") + sess("f", 16, 2, "future")
    r = analyze(ss, Policy(parse_min_frequency("75%"), total_hours=F(72)))
    assert r["required_present_hours"] == 54
    assert r["absence_limit_hours"] == 18
    assert r["absence_margin_hours"] == 8
    assert r["can_skip"]["guaranteed"]["max_meetings"] == 4  # only 4 because each meeting is 2 hours
    assert r["hours_still_needed_to_reach_minimum"] == 24


def test_30h_present_needs_24_more():
    ss = sess("p", 15, 2, "present") + sess("f", 21, 2, "future")
    r = analyze(ss, Policy(parse_min_frequency("0.75"), total_hours=F(72)))
    assert r["hours_still_needed_to_reach_minimum"] == 24
    assert r["minimum_already_impossible"] is False


def test_pending_roll_calls_and_negative_margin_and_impossible():
    ss = sess("a", 10, 2, "absent") + sess("n", 2, 2, "pending") + sess("f", 4, 2, "future")
    r = analyze(ss, Policy(parse_min_frequency("75%"), total_hours=F(72)))
    assert r["pending_hours"] == 4 and r["pending_sessions"] == ["n0", "n1"]
    assert r["absence_margin_hours"] == -2  # 20h absent > 18h limit
    assert r["minimum_already_impossible"] is True


def test_different_weights_and_cancelled_and_critical():
    ss = [
        Session("a", None, F(2), "absent"),
        Session("c", None, F(4), "cancelled"),
        Session("p", None, F(2), "present"),
        Session("f1", None, F(4), "future"),
        Session("f2", None, F(1), "future"),
    ]
    r = analyze(ss, Policy(parse_min_frequency("50%"), total_hours=F(9)))
    # total 9 -> required ceil(4.5)=5 present, limit 4, one absent 2h -> margin 2
    assert r["required_present_hours"] == 5 and r["absence_margin_hours"] == 2
    assert r["registered"]["excluded_hours"] == 4
    assert "f1" in r["critical_future_sessions"] and "f2" not in r["critical_future_sessions"]


def test_scenario_skip_specific_session_and_unknown_total_is_provisional():
    ss = sess("p", 4, 2, "present") + sess("f", 4, 2, "future")
    r = analyze(ss, Policy(parse_min_frequency("75%")), skip_ids={"f0", "f1", "f2"})
    assert any("provisional" in w for w in r["warnings"])
    assert r["scenario"]["meets_minimum"] is False  # 10 of 16 hours < 12 required
    r2 = analyze(ss, Policy(parse_min_frequency("75%"), total_hours=F(16)), skip_ids={"f0"})
    assert r2["scenario"]["projected_final_frequency"] == 0.875 and r2["scenario"]["meets_minimum"] is True


def test_policy_late_and_excused():
    ss = [
        Session("l", None, F(2), "late"),
        Session("e", None, F(2), "excused"),
        Session("p", None, F(2), "present"),
    ]
    r = analyze(ss, Policy(F(3, 4), late="absent", excused="excluded", total_hours=F(6)))
    assert r["registered"]["absence_hours"] == 2 and r["registered"]["excluded_hours"] == 2
    assert r["rule"]["total_hours"] == 4


# --------------------------------------------------------------------------- grades
SCHEME = {
    "items": [
        {"name": "P1", "score": 7, "max": 10, "weight": 1},
        {"name": "P2", "score": None, "max": 10, "weight": 1},
    ],
    "formula": "avg(P1, P2)",
    "pass_mark": 6,
    "rounding": {"step": "0.5"},
}


def test_missing_grade_is_not_zero():
    r = g.compute(SCHEME)
    assert r["status"] == "conditional" and r["missing_items"] == ["P2"]
    assert r["best_case"] == "8.5" and r["worst_case"] == "3.5" and r["passing_still_possible"] is True


def test_zero_differs_from_missing():
    s = {**SCHEME, "items": [SCHEME["items"][0], {"name": "P2", "score": 0, "max": 10, "weight": 1}]}
    r = g.compute(s)
    assert r["status"] == "complete" and r["value"] == "3.5" and r["passes"] is False


def test_rounding_half_up_to_step():
    assert g.round_step(g.D("7.25"), g.D("0.5")) == g.D("7.5")
    assert g.round_step(g.D("6.74"), g.D("0.5")) == g.D("6.5")
    assert g.round_step(g.D("2.5"), g.D("1"), "half_even") == g.D("2")
    assert g.round_step(g.D("2.5"), g.D("1")) == g.D("3")


def test_required_score_and_impossible():
    r = g.required_score(SCHEME, "P2")
    # (7 + 4.5)/2 = 5.75 -> rounds to 6.0 with the 0.5 step, so 4.5 already reaches the pass mark
    assert r["status"] == "ok" and g.D(r["required"]) == g.D("4.50") and r["resulting_value"] == "6.0"
    s = {**SCHEME, "pass_mark": 9.5}
    assert g.required_score(s, "P2")["status"] == "impossible"


def test_required_score_underdetermined_needs_choice():
    s = {
        "items": [
            {"name": "A", "score": None, "max": 10, "weight": 1},
            {"name": "B", "score": None, "max": 10, "weight": 1},
        ],
        "formula": "weighted",
        "pass_mark": 6,
    }
    assert g.required_score(s, "A")["status"] == "underdetermined"
    assert g.required_score(s, "A", fill_others="best")["required"] == "2.00"


def test_conditional_formula_recovery_and_drop_lowest():
    s = {
        "items": [
            {"name": "N1", "score": 4, "max": 10},
            {"name": "N2", "score": 5, "max": 10},
            {"name": "N3", "score": 9, "max": 10},
            {"name": "REC", "score": 7, "max": 10},
        ],
        "formula": "if(1, 0, 0)",
        "pass_mark": 6,
    }
    s["formula"] = "round_to(drop_lowest(1, N1, N2, N3), 0.5)"
    assert g.compute(s)["value"] == "7.0"
    s["formula"] = "(avg(N1,N2,N3) + REC)/2 if avg(N1,N2,N3) >= 3 and avg(N1,N2,N3) < 6 else avg(N1,N2,N3)"
    assert g.compute(s)["value"] == "6"


@pytest.mark.parametrize(
    "evil",
    [
        "__import__('os').system('echo x')",
        "open('/etc/passwd')",
        "().__class__",
        "N1.real",
        "[x for x in (1,2)]",
        "lambda: 1",
        "N1 ** 9999999",
        "exec('1')",
        "N1; 1",
    ],
)
def test_formula_is_not_eval(evil):
    s = {"items": [{"name": "N1", "score": 4, "max": 10}], "formula": evil}
    with pytest.raises(g.FormulaError):
        g.compute(s)


# --------------------------------------------------------------------------- windows
def test_next_week_is_not_next_7_days():
    wed = datetime(2026, 11, 4, 15, 30, tzinfo=TZ)  # a Wednesday
    nw = window("next_week", TZ, wed)
    n7 = window("next_7_days", TZ, wed)
    assert nw.start == datetime(2026, 11, 9, 0, 0, tzinfo=TZ) and nw.end == datetime(
        2026, 11, 16, 0, 0, tzinfo=TZ
    )
    assert n7.start == wed and (n7.end - n7.start).days == 7
    assert nw.start != n7.start
    assert nw.to_dict()["start"].endswith("-03:00")


def test_custom_window_bare_date_includes_whole_end_day():
    w = window("custom", TZ, start="2026-11-10", end="2026-11-10")
    assert (w.end - w.start).total_seconds() == 86400
