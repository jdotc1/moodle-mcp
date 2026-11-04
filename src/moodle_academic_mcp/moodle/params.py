"""Encode nested Python structures the way Moodle's REST server expects (PHP-style form fields)."""

from __future__ import annotations

from typing import Any


def flatten(params: dict[str, Any] | None) -> list[tuple[str, str]]:
    """{"a": [{"b": 1}]} -> [("a[0][b]", "1")]. Booleans become 1/0, None is skipped."""
    out: list[tuple[str, str]] = []

    def walk(prefix: str, value: Any) -> None:
        if value is None:
            return
        if isinstance(value, bool):
            out.append((prefix, "1" if value else "0"))
        elif isinstance(value, dict):
            for k, v in value.items():
                walk(f"{prefix}[{k}]" if prefix else str(k), v)
        elif isinstance(value, (list, tuple)):
            for i, v in enumerate(value):
                walk(f"{prefix}[{i}]", v)
        else:
            out.append((prefix, str(value)))

    for k, v in (params or {}).items():
        walk(str(k), v)
    return out
