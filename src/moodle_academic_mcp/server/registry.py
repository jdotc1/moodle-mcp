"""Single registry of operations. MCP tools and the HTTP/OpenAPI API are generated from it,
so behaviour and descriptions are never duplicated."""

from __future__ import annotations

import inspect
import re
import typing
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Any

from pydantic import Field


@dataclass
class Op:
    name: str
    fn: Callable[..., Awaitable[Any]]
    description: str
    read_only: bool
    destructive: bool = False
    idempotent: bool = True
    tags: list[str] = field(default_factory=list)
    param_docs: dict[str, str] = field(default_factory=dict)

    def public_signature(self) -> inspect.Signature:
        """Signature without the leading Account argument, plus an optional `profile`; annotations resolved and
        enriched with descriptions."""
        hints = typing.get_type_hints(self.fn, include_extras=False)
        params = []
        for i, (pname, p) in enumerate(inspect.signature(self.fn).parameters.items()):
            if i == 0:
                continue  # acct
            ann = hints.get(pname, Any)
            desc = self.param_docs.get(pname)
            if desc:
                ann = Annotated[ann, Field(description=desc)]
            params.append(p.replace(annotation=ann))
        # keyword-only optional profile selector
        params = [p for p in params if p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)]
        required = [p for p in params if p.default is p.empty]
        optional = [p for p in params if p.default is not p.empty]
        prof = inspect.Parameter(
            "profile",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            default=None,
            annotation=Annotated[
                str | None, Field(description="Institution profile name (omit if you have one).")
            ],
        )
        return inspect.Signature([*required, *optional, prof])


OPS: dict[str, Op] = {}


def _parse_doc(doc: str) -> tuple[str, dict[str, str]]:
    doc = inspect.cleandoc(doc or "")
    if "Args:" not in doc:
        return doc, {}
    head, _, tail = doc.partition("Args:")
    docs: dict[str, str] = {}
    cur = None
    for line in tail.splitlines():
        m = re.match(r"^\s{0,4}(\w+)\s*:\s*(.*)$", line)
        if m and not line.startswith("        "):
            cur = m.group(1)
            docs[cur] = m.group(2).strip()
        elif cur and line.strip():
            docs[cur] += " " + line.strip()
    return head.strip(), docs


def op(
    name: str | None = None,
    *,
    read_only: bool = True,
    destructive: bool = False,
    idempotent: bool = True,
    tags: list[str] | None = None,
):  # type: ignore[no-untyped-def]
    def deco(fn: Callable[..., Awaitable[Any]]):  # type: ignore[no-untyped-def]
        desc, pdocs = _parse_doc(fn.__doc__ or "")
        o = Op(name or fn.__name__, fn, desc, read_only, destructive, idempotent, tags or [], pdocs)
        if o.name in OPS:
            raise RuntimeError(f"duplicate op {o.name}")
        OPS[o.name] = o
        return fn

    return deco
