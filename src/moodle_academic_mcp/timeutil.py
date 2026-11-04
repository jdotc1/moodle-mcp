"""Timezone-aware helpers. All outputs carry an explicit offset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo


def to_dt(ts: int | float | None, tz: ZoneInfo) -> datetime | None:
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), tz=UTC).astimezone(tz)


def iso(ts: int | float | None, tz: ZoneInfo) -> str | None:
    dt = to_dt(ts, tz)
    return dt.isoformat() if dt else None


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime  # exclusive
    label: str

    def to_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "label": self.label,
            "timezone": str(self.start.tzinfo),
        }

    @property
    def ts(self) -> tuple[int, int]:
        return int(self.start.timestamp()), int(self.end.timestamp())


def window(
    kind: str, tz: ZoneInfo, now: datetime | None = None, *, start: str | None = None, end: str | None = None
) -> Window:
    """Resolve a named range. `next_week` is the next calendar week (Mon-Sun, week starts Monday);
    `next_7_days` is a rolling window from now. They are deliberately different."""
    now = (now or datetime.now(tz)).astimezone(tz)
    today0 = datetime.combine(now.date(), time.min, tzinfo=tz)
    if kind == "next_7_days":
        return Window(now, now + timedelta(days=7), "next_7_days (rolling from now)")
    if kind == "today":
        return Window(today0, today0 + timedelta(days=1), "today")
    if kind == "this_week":
        mon = today0 - timedelta(days=now.weekday())
        return Window(mon, mon + timedelta(days=7), "this_week (Mon-Sun)")
    if kind == "next_week":
        mon = today0 - timedelta(days=now.weekday()) + timedelta(days=7)
        return Window(mon, mon + timedelta(days=7), "next_week (next calendar week, Mon-Sun)")
    if kind == "next_30_days":
        return Window(now, now + timedelta(days=30), "next_30_days (rolling from now)")
    if kind == "custom":
        if not start or not end:
            raise ValueError("custom window needs start and end (ISO dates)")
        s = parse_dt(start, tz)
        e = parse_dt(end, tz)
        if len(end) <= 10:  # a bare date means the whole day
            e = e + timedelta(days=1)
        return Window(s, e, "custom")
    raise ValueError(f"unknown window kind {kind!r}")


def parse_dt(value: str, tz: ZoneInfo) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(tz)
