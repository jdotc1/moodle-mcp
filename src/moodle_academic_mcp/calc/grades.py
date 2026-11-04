"""Deterministic grade arithmetic with Decimal and a restricted expression language (no eval).

A scheme is plain data:
  {"items": [{"name": "P1", "score": 7.5, "max": 10, "weight": 0.3}, {"name": "P2", "score": null, ...}],
   "formula": "avg(P1, P2)"            # or "weighted" (uses item weights)
   "rounding": {"step": "0.5", "mode": "half_up"},   # optional, applied to the final value
   "pass_mark": 6,
   "source": "plano de ensino, secao 5 (2026-03-10)"}
A `None` score means *not graded yet* - it is never treated as zero except explicitly in the 'worst case'.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from typing import Any

D = Decimal
_MODES = {
    "half_up": ROUND_HALF_UP,
    "half_even": ROUND_HALF_EVEN,
    "down": ROUND_DOWN,
    "up": ROUND_CEILING,
    "floor": ROUND_FLOOR,
}


class FormulaError(ValueError):
    pass


def dec(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        return v
    if isinstance(v, bool):
        raise FormulaError("boolean is not a number here")
    return D(str(v).strip().replace(",", "."))


def round_step(x: Decimal, step: Decimal = D("1"), mode: str = "half_up") -> Decimal:
    """Round to a multiple of `step` (step=0.5 is the common 'nearest half point')."""
    if step <= 0:
        raise FormulaError("step must be positive")
    q = (x / step).quantize(D("1"), rounding=_MODES[mode])
    return q * step


# ----------------------------------------------------------------------------- safe evaluator
_ALLOWED_BIN = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
}
_ALLOWED_CMP = {
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
}


def _flat(args: tuple) -> list[Decimal]:
    out: list[Decimal] = []
    for a in args:
        out.extend(_flat(tuple(a)) if isinstance(a, (list, tuple)) else [dec(a)])
    return out


def _f_avg(*a: Any) -> Decimal:
    v = _flat(a)
    if not v:
        raise FormulaError("avg() of nothing")
    return sum(v, D(0)) / len(v)


def _f_drop_lowest(n: Any, *a: Any) -> Decimal:
    """Average after dropping the n lowest values."""
    v = sorted(_flat(a))
    k = int(n)
    v = v[k:]
    if not v:
        raise FormulaError("drop_lowest() dropped everything")
    return sum(v, D(0)) / len(v)


FUNCS = {
    "avg": _f_avg,
    "sum": lambda *a: sum(_flat(a), D(0)),
    "min": lambda *a: min(_flat(a)),
    "max": lambda *a: max(_flat(a)),
    "drop_lowest": _f_drop_lowest,
    "round_half_up": lambda x, places=0: dec(x).quantize(D(1).scaleb(-int(places)), rounding=ROUND_HALF_UP),
    "round_to": lambda x, step, mode="half_up": round_step(dec(x), dec(step), str(mode)),
}


class _Eval:
    def __init__(self, env: dict[str, Decimal]):
        self.env = env

    def ev(self, node: ast.AST) -> Any:  # noqa: C901
        if isinstance(node, ast.Expression):
            return self.ev(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float, str)):
                raise FormulaError("unsupported literal")
            return dec(node.value) if not isinstance(node.value, str) else node.value
        if isinstance(node, ast.Name):
            if node.id not in self.env:
                raise FormulaError(f"unknown name {node.id!r}")
            return self.env[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BIN:
            a, b = self.ev(node.left), self.ev(node.right)
            if isinstance(node.op, ast.Div) and b == 0:
                raise FormulaError("division by zero")
            return _ALLOWED_BIN[type(node.op)](a, b)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd, ast.Not)):
            v = self.ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else (not v if isinstance(node.op, ast.Not) else v)
        if isinstance(node, ast.Compare):
            left = self.ev(node.left)
            for op, comp in zip(node.ops, node.comparators, strict=True):
                right = self.ev(comp)
                if type(op) not in _ALLOWED_CMP or not _ALLOWED_CMP[type(op)](left, right):
                    if type(op) not in _ALLOWED_CMP:
                        raise FormulaError("unsupported comparison")
                    return False
                left = right
            return True
        if isinstance(node, ast.BoolOp):
            vals = [self.ev(v) for v in node.values]
            return all(vals) if isinstance(node.op, ast.And) else any(vals)
        if isinstance(node, ast.IfExp):
            return self.ev(node.body) if self.ev(node.test) else self.ev(node.orelse)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in FUNCS
            and not node.keywords
        ):
            return FUNCS[node.func.id](*[self.ev(a) for a in node.args])  # type: ignore[operator]
        raise FormulaError(f"unsupported expression element: {type(node).__name__}")


def evaluate(expr: str, env: dict[str, Decimal]) -> Decimal:
    if len(expr) > 2000:
        raise FormulaError("formula too long")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise FormulaError(f"syntax error: {e.msg}") from None
    out = _Eval(env).ev(tree)
    if isinstance(out, bool):
        raise FormulaError("formula must return a number")
    return dec(out)


# ----------------------------------------------------------------------------- schemes
@dataclass
class Item:
    name: str
    score: Decimal | None
    max: Decimal
    weight: Decimal | None


def _items(scheme: dict) -> list[Item]:
    items = []
    seen = set()
    for it in scheme["items"]:
        n = it["name"]
        if not n.isidentifier() or n in FUNCS or n in seen:
            raise FormulaError(f"item name {n!r} must be a unique identifier and not a function name")
        seen.add(n)
        items.append(
            Item(
                n,
                None if it.get("score") is None else dec(it["score"]),
                dec(it.get("max", 10)),
                None if it.get("weight") is None else dec(it["weight"]),
            )
        )
    return items


def _formula(scheme: dict, items: list[Item]) -> str:
    f = scheme.get("formula", "weighted")
    if f == "weighted":
        if any(i.weight is None for i in items):
            raise FormulaError("'weighted' needs a weight on every item")
        tw = sum((i.weight for i in items if i.weight is not None), D(0))
        if tw == 0:
            raise FormulaError("weights add up to zero")
        return "(" + " + ".join(f"{i.name}*{i.weight}" for i in items) + f")/{tw}"
    return f


def _finish(v: Decimal, scheme: dict) -> Decimal:
    r = scheme.get("rounding")
    if r:
        return round_step(v, dec(r.get("step", "1")), r.get("mode", "half_up"))
    return v


def compute(scheme: dict, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compute the current result. Missing scores are *not* guessed: the result is conditional with bounds."""
    items = _items(scheme)
    expr = _formula(scheme, items)
    ov = {k: dec(v) for k, v in (overrides or {}).items()}
    known = {i.name: (ov.get(i.name, i.score)) for i in items}
    missing = [n for n, v in known.items() if v is None]
    res: dict[str, Any] = {
        "formula": expr,
        "source": scheme.get("source"),
        "pass_mark": scheme.get("pass_mark"),
        "rounding": scheme.get("rounding"),
        "missing_items": missing,
        "assumptions": [f"{k} = {v} (hypothetical)" for k, v in ov.items()],
    }
    if not missing:
        raw = evaluate(expr, {n: v for n, v in known.items() if v is not None})
        val = _finish(raw, scheme)
        res.update(
            status="complete",
            raw=str(raw.quantize(D("0.0001"))),
            value=str(val),
            passes=None if scheme.get("pass_mark") is None else val >= dec(scheme["pass_mark"]),
        )
        return res
    maxes = {i.name: i.max for i in items}
    low = _finish(evaluate(expr, {n: (v if v is not None else D(0)) for n, v in known.items()}), scheme)
    high = _finish(evaluate(expr, {n: (v if v is not None else maxes[n]) for n, v in known.items()}), scheme)
    pm = scheme.get("pass_mark")
    res.update(
        status="conditional",
        worst_case=str(low),
        best_case=str(high),
        note="Not-yet-graded items are not zeros: bounds assume 0 or the item maximum.",
    )
    if pm is not None:
        pmd = dec(pm)
        res["passes_in_worst_case"] = low >= pmd
        res["passing_still_possible"] = high >= pmd
    return res


def required_score(
    scheme: dict,
    target_item: str,
    target: Any | None = None,
    *,
    step: Any = "0.01",
    fill_others: str | None = None,
) -> dict[str, Any]:
    """Minimum score on `target_item` so the final value reaches `target` (default: pass_mark).

    Other missing items must be provided via fill_others ('worst'=0, 'best'=max) or the question is
    underdetermined and a conditional answer is returned. Assumes the formula is non-decreasing in the item
    (checked on the endpoints and midpoint)."""
    items = _items(scheme)
    expr = _formula(scheme, items)
    by = {i.name: i for i in items}
    if target_item not in by:
        raise FormulaError(f"unknown item {target_item!r}")
    goal = dec(target if target is not None else scheme.get("pass_mark"))
    st = dec(step)
    env: dict[str, Decimal] = {}
    notes = []
    for i in items:
        if i.name == target_item:
            continue
        if i.score is None:
            if fill_others is None:
                return {
                    "status": "underdetermined",
                    "formula": expr,
                    "message": f"Item {i.name!r} is also not graded; choose fill_others='worst' or 'best', or "
                    "supply a hypothetical score.",
                    "missing_items": [x.name for x in items if x.score is None],
                }
            env[i.name] = D(0) if fill_others == "worst" else i.max
            notes.append(f"{i.name} assumed {'0' if fill_others == 'worst' else 'its maximum'}")
        else:
            env[i.name] = i.score

    def at(x: Decimal) -> Decimal:
        return _finish(evaluate(expr, {**env, target_item: x}), scheme)

    lo, hi = D(0), by[target_item].max
    v_lo, v_mid, v_hi = at(lo), at((lo + hi) / 2), at(hi)
    if not (v_lo <= v_mid <= v_hi):
        return {
            "status": "unsupported",
            "message": "Formula is not monotonic in this item; cannot solve safely.",
        }
    base = {
        "formula": expr,
        "target": str(goal),
        "item": target_item,
        "max_for_item": str(hi),
        "assumptions": notes,
        "source": scheme.get("source"),
    }
    if v_hi < goal:
        return {**base, "status": "impossible", "best_possible": str(v_hi)}
    if v_lo >= goal:
        return {**base, "status": "already_met", "required": "0"}
    n_lo, n_hi = 0, int((hi / st).to_integral_value(rounding=ROUND_CEILING))
    while n_lo < n_hi:  # smallest multiple of `step` that reaches the goal
        mid = (n_lo + n_hi) // 2
        if at(min(mid * st, hi)) >= goal:
            n_hi = mid
        else:
            n_lo = mid + 1
    need = min(n_lo * st, hi)
    return {**base, "status": "ok", "required": str(need), "resulting_value": str(at(need))}
