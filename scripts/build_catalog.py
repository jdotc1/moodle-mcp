"""Build a function catalog (names, descriptions, REST parameter shapes) from a Moodle 'wsdoc' PDF export.

Usage:  pdftotext -layout wsdoc.pdf out.txt && python scripts/build_catalog.py out.txt OUT.json --source "..." --date 2026-11-03

Only API *shapes* are extracted (function names, parameter names/types, descriptions). The PDF itself, the user
name in its header and any account data are never copied.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

NAME_LINE = re.compile(r"^   ([a-z]+(?:_[a-z0-9]+)+) *$")
ARG_HDR = re.compile(r"^   (\w+) \((Obrigatório|Padrão para.*)\)\s*$")
REST_PARAM = re.compile(r"^\s{4,}([\w\[\]]+)= ?(\w*)\s*$")


def parse(lines: list[str]) -> dict[str, dict]:
    idx: dict[str, int] = {}
    for i, ln in enumerate(lines):
        m = NAME_LINE.match(ln)
        if m:
            idx[m.group(1)] = i  # last occurrence = the detail section (first is the index)
    order = sorted(idx.items(), key=lambda kv: kv[1])
    out: dict[str, dict] = {}
    for n, (name, start) in enumerate(order):
        end = order[n + 1][1] if n + 1 < len(order) else len(lines)
        block = [ln for ln in lines[start + 1 : end] if ln.strip() and "wsdoc.php" not in ln
                 and "Web documentação de serviço" not in ln]
        desc = block[0].strip() if block else ""
        params: list[dict] = []
        required: dict[str, bool] = {}
        cur: str | None = None
        in_resp = False
        for ln in block[1:]:
            if ln.strip().startswith("Resposta"):
                in_resp = True
            if in_resp:
                continue
            m = ARG_HDR.match(ln)
            if m:
                cur = m.group(1)
                required[cur] = m.group(2) == "Obrigatório"
                continue
            m = REST_PARAM.match(ln)
            if m and cur:
                path = m.group(1)
                top = re.split(r"\[", path, 1)[0]
                params.append({"name": path, "type": m.group(2) or "string", "top": top,
                               "required": required.get(top, False)})
        if "Argumentos" not in "\n".join(block[:3]) and not params:
            pass
        out[name] = {"description": desc, "params": params}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("txt")
    ap.add_argument("out")
    ap.add_argument("--source", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--service", default="mobile")
    a = ap.parse_args()
    lines = open(a.txt, encoding="utf-8").read().splitlines()
    funcs = parse(lines)
    doc = {
        "provenance": {"source": a.source, "exported": a.date, "service": a.service,
                       "note": "Documented for the service of the exporting account; documentation is not proof "
                               "that every account may call every function."},
        "functions": funcs,
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"{len(funcs)} functions", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
