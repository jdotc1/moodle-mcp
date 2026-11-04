"""Exact attendance arithmetic (fractions, no floats). Pure functions: no Moodle, no I/O.

Concepts kept apart on purpose:
  registered absences   - what the teacher already recorded
  pending roll calls    - past sessions with no record yet (outcome unknown)
  projection            - what happens under explicit hypotheses
Attendance *points* in the gradebook, completion state and official presence are NOT the same thing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from fractions import Fraction
from typing import Any

STATUS_CATEGORIES = {"present", "late", "absent", "excused", "cancelled", "pending", "future", "unknown"}


def to_fraction(v: Any) -> Fraction:
    if isinstance(v, Fraction):
        return v
    if isinstance(v, float):
        return Fraction(Decimal(str(v)))
    s = str(v).strip()
    if s.endswith("%"):
        return Fraction(Decimal(s[:-1].strip())) / 100
    return Fraction(Decimal(s))


def parse_min_frequency(v: Any) -> Fraction:
    """'75%', '75' (>1 means percent), '0.75', 0.75, '3/4' -> Fraction in (0, 1]."""
    s = str(v).strip()
    f = Fraction(s) if "/" in s else to_fraction(s)
    if f > 1:
        f = f / 100
    if not (0 < f <= 1):
        raise ValueError("minimum frequency must be in (0, 1] or a percentage")
    return f


def _fmt(f: Fraction | None) -> int | float | None:
    if f is None:
        return None
    return int(f) if f.denominator == 1 else float(round(Decimal(f.numerator) / Decimal(f.denominator), 4))


@dataclass
class Session:
    id: str
    when: date | None
    weight: Fraction  # class-hours (or any unit) this meeting accounts for
    category: str  # one of STATUS_CATEGORIES
    label: str = ""

    def __post_init__(self) -> None:
        if self.category not in STATUS_CATEGORIES:
            raise ValueError(f"unknown category {self.category!r}")
        if self.weight < 0:
            raise ValueError("session weight cannot be negative")


@dataclass
class Policy:
    min_frequency: Fraction
    late: str = "present"  # present | absent | half
    excused: str = "absent"  # absent | present | excluded (abono removes the hours from the total)
    total_hours: Fraction | None = None  # official total of the course; None = unknown
    source: str = ""  # where the rule came from (mandatory for UFSC reports)


@dataclass
class AttendanceReport:
    data: dict[str, Any] = field(default_factory=dict)


def analyze(
    sessions: list[Session],
    policy: Policy,
    *,
    skip_ids: set[str] | None = None,
    assume_pending: str = "unknown",
    assume_future: Fraction = Fraction(1),
) -> dict[str, Any]:
    """Compute the full attendance picture.

    skip_ids        future (or pending) sessions the student hypothetically misses
    assume_pending  'unknown' (report range) | 'present' | 'absent'
    assume_future   share of the remaining future hours attended in the projection (1 = attend all)
    """
    skip_ids = skip_ids or set()
    present = absent = pending = future = excluded = Fraction(0)
    warnings: list[str] = []
    future_sessions: list[Session] = []
    pending_sessions: list[Session] = []
    unknown_labels: list[str] = []

    for s in sessions:
        c, w = s.category, s.weight
        if c == "late":
            c = {"present": "present", "absent": "absent", "half": "half"}[policy.late]
        if c == "excused":
            c = {"absent": "absent", "present": "present", "excluded": "excluded"}[policy.excused]
        if c == "present":
            present += w
        elif c == "half":
            present += w / 2
            absent += w / 2
        elif c == "absent":
            absent += w
        elif c in ("cancelled", "excluded"):
            excluded += w
        elif c == "pending":
            pending += w
            pending_sessions.append(s)
        elif c == "future":
            future += w
            future_sessions.append(s)
        else:
            unknown_labels.append(s.label or s.id)
            pending += w
            pending_sessions.append(s)

    if unknown_labels:
        warnings.append(
            "Sessions with an unrecognised status were treated as pending: " + ", ".join(unknown_labels)
        )

    taken = present + absent
    listed_total = (
        present + absent + pending + future
    )  # excluded (cancelled/abonoed) hours are out of the total
    total = policy.total_hours
    if total is not None and policy.excused == "excluded":
        # abono: official total shrinks by the excluded excused hours only (cancelled classes never counted)
        excused_hours = sum((s.weight for s in sessions if s.category == "excused"), Fraction(0))
        total = total - excused_hours
    total_known = total is not None
    if total is None:
        total = listed_total
        warnings.append(
            "Total course hours unknown: totals were derived from the sessions listed so far, so "
            "limits are provisional."
        )
    elif listed_total != total:
        warnings.append(
            f"Listed sessions add up to {_fmt(listed_total)} but the course total is {_fmt(total)}; "
            "some sessions may not be published yet."
        )

    min_f = policy.min_frequency
    required = min_f * total
    all_int = total.denominator == 1 and all(s.weight.denominator == 1 for s in sessions)
    if all_int:
        required = Fraction(math.ceil(required))  # ceil(freq * total) for whole units
    max_absence = total - required
    margin = max_absence - absent
    needed = max(Fraction(0), required - present)
    # Hours that can still be attended: future sessions (+ pending ones if the roll call turns out positive).
    unassigned_future = total - taken - pending if total_known else future
    attendable_future = min(future, max(unassigned_future, Fraction(0))) if total_known else future
    best_case_present = (
        present + pending + (future if not total_known else max(total - taken - pending, Fraction(0)))
    )
    best_case_present = min(best_case_present, total)
    impossible_now = best_case_present < required
    impossible_if_pending_absent = (present + (total - taken - pending if total_known else future)) < required

    # Skippable hours
    margin_pending_worst = margin - pending  # pending roll calls all turn out as absences
    pending_dec = {"unknown": None, "present": Fraction(0), "absent": pending}[assume_pending]

    def skippable(m: Fraction) -> dict[str, Any]:
        hrs = max(Fraction(0), min(m, future))
        weights = sorted(s.weight for s in future_sessions if s.weight > 0)
        count, acc = 0, Fraction(0)
        for w in weights:
            if acc + w <= m:
                acc += w
                count += 1
            else:
                break
        return {"hours": _fmt(hrs), "max_meetings": count if future_sessions else None}

    skip_guaranteed = skippable(margin_pending_worst)
    skip_if_pending_ok = skippable(margin)

    # Which future meetings are critical (skipping that one alone breaks the minimum)?
    critical = [s.id for s in future_sessions if s.weight > max(margin_pending_worst, Fraction(0))]

    # Hypothetical scenario
    skipped_w = sum(
        (s.weight for s in sessions if s.id in skip_ids and s.category in ("future", "pending")), Fraction(0)
    )
    projected_future_attended = (future - skipped_w) if skip_ids else future * assume_future
    p_pending = pending_dec if pending_dec is not None else Fraction(0)
    proj_present = present + (pending - p_pending) + projected_future_attended
    proj_abs = absent + p_pending + (future - projected_future_attended)
    proj_total = total if total_known else (proj_present + proj_abs)
    proj_freq = proj_present / proj_total if proj_total else None
    scenario_ok = proj_present >= required if proj_total else None
    scenario_pending_note = (
        ("pending roll calls assumed " + assume_pending)
        if assume_pending != "unknown"
        else "pending roll calls assumed present in the scenario (best case); see 'pending_hours'"
    )

    freq_so_far = (present / taken) if taken else None
    out = {
        "units": "class-hours (or the weight unit supplied)",
        "rule": {
            "min_frequency": _fmt(min_f),
            "min_frequency_percent": _fmt(min_f * 100),
            "source": policy.source,
            "late_counts_as": policy.late,
            "excused_counts_as": policy.excused,
            "total_hours": _fmt(total),
            "total_known": total_known,
        },
        "registered": {
            "present_hours": _fmt(present),
            "absence_hours": _fmt(absent),
            "excluded_hours": _fmt(excluded),
        },
        "pending_hours": _fmt(pending),
        "pending_sessions": [s.id for s in pending_sessions],
        "future_hours": _fmt(future),
        "frequency_so_far": _fmt(freq_so_far)
        if freq_so_far is None
        else float(round(Decimal(freq_so_far.numerator) / Decimal(freq_so_far.denominator), 4)),
        "required_present_hours": _fmt(required),
        "absence_limit_hours": _fmt(max_absence),
        "absence_margin_hours": _fmt(margin),
        "margin_if_all_pending_are_absences": _fmt(margin_pending_worst),
        "hours_still_needed_to_reach_minimum": _fmt(needed),
        "attendable_hours_left": _fmt(attendable_future),
        "minimum_already_impossible": impossible_now,
        "minimum_impossible_if_pending_are_absences": impossible_if_pending_absent,
        "can_skip": {"guaranteed": skip_guaranteed, "if_pending_are_present": skip_if_pending_ok},
        "critical_future_sessions": critical,
        "scenario": {
            "skipped_sessions": sorted(skip_ids),
            "assume_future_attended_share": _fmt(assume_future),
            "projected_present_hours": _fmt(proj_present),
            "projected_absence_hours": _fmt(proj_abs),
            "projected_final_frequency": None
            if proj_freq is None
            else float(round(Decimal(proj_freq.numerator) / Decimal(proj_freq.denominator), 4)),
            "meets_minimum": scenario_ok,
            "note": scenario_pending_note,
        },
        "warnings": warnings,
        "caveats": [
            "Does not know about exams, presentations or other mandatory sessions unless they are listed.",
            "Official presence is recorded by the institution; this is an estimate from the data available.",
        ],
    }
    return out
