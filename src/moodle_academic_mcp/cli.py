"""Command line: configure, doctor, capabilities, serve, version (+ admin helpers)."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import os
import secrets
import sys
import time
from typing import Any

from . import __version__
from .config import (
    AppConfig,
    LocalTokenStore,
    ProfileSettings,
    builtin_profile_presets,
    normalize_base_url,
    validate_profile_name,
)
from .errors import MamcpError
from .redact import scrub, setup_logging


def _print(obj: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(scrub(obj), indent=2, ensure_ascii=False, default=str))
    else:
        _pretty(scrub(obj))


def _pretty(o: Any, indent: int = 0) -> None:
    pad = "  " * indent
    if isinstance(o, dict):
        for k, v in o.items():
            if isinstance(v, (dict, list)) and v:
                print(f"{pad}{k}:")
                _pretty(v, indent + 1)
            else:
                print(f"{pad}{k}: {v}")
    elif isinstance(o, list):
        for v in o:
            if isinstance(v, (dict, list)):
                print(f"{pad}-")
                _pretty(v, indent + 1)
            else:
                print(f"{pad}- {v}")
    else:
        print(f"{pad}{o}")


# ------------------------------------------------------------------------------------------ configure
def cmd_configure(a: argparse.Namespace) -> int:
    cfg = AppConfig.load()
    tokens = LocalTokenStore()
    presets = builtin_profile_presets()
    name = a.profile
    if not name:
        print("Presets:", ", ".join(presets) or "none", "| or type a new profile name")
        name = input("Profile name [ufsc]: ").strip() or "ufsc"
    validate_profile_name(name)
    existing = cfg.profiles.get(name)
    preset = presets.get(name, {})
    url = a.url or (existing.base_url if existing else None) or preset.get("base_url")
    if not url:
        url = input("Moodle base URL (https://...): ").strip()
    insecure = bool(a.allow_insecure_http)
    p = ProfileSettings(
        name=name,
        base_url=normalize_base_url(url, allow_insecure_http=insecure),
        institution=(existing.institution if existing else preset.get("institution", name)),
        timezone=a.timezone or (existing.timezone if existing else preset.get("timezone", "UTC")),
        allow_insecure_http=insecure,
        allow_private_networks=bool(a.allow_private_networks)
        or bool(existing and existing.allow_private_networks),
        attendance_min_percent=existing.attendance_min_percent if existing else None,
        attendance_rule_source=existing.attendance_rule_source if existing else None,
    )
    cfg.profiles[name] = p
    cfg.default_profile = cfg.default_profile or name
    cfg.save()
    print(f"\nProfile '{name}' saved to {cfg.path}")
    print(f"Create tokens at: {p.base_url}/user/managetoken.php  (one per service: mobile, attendance, vpl)")
    print(
        "Tokens are read with hidden input and stored in the OS keyring when available, otherwise in a "
        "private (0600) file. They are never printed.\n"
    )
    for service in a.service or ["mobile"]:
        if sys.stdin.isatty():
            tok = getpass.getpass(f"Token for service '{service}' (empty = keep/skip): ").strip()
        else:
            tok = sys.stdin.readline().strip()
        if tok:
            where = tokens.set(name, service, tok)
            print(f"  {service}: stored in {where}")
    print("\nNext: moodle-academic-mcp doctor")
    return 0


# ------------------------------------------------------------------------------------------ doctor
async def _doctor(a: argparse.Namespace) -> int:
    from .bootstrap import local_resolver
    from .domain.doctor import diagnose, probe_endpoint

    cfg = AppConfig.load()
    if not cfg.profiles:
        print("No profile configured. Run: moodle-academic-mcp configure", file=sys.stderr)
        return 2
    profile = cfg.profile(a.profile)
    res = local_resolver(cfg)
    await res.store.init()
    try:
        out: dict[str, Any]
        try:
            acct = await res.account(profile.name)
            out = await diagnose(acct, detect=not a.no_detect)
        except MamcpError as e:
            out = {
                "profile": profile.to_public(),
                "endpoint": await probe_endpoint(profile),
                "error": e.to_dict(),
            }
        _print(out, a.json)
        ok = out.get("endpoint", {}).get("ok") and all(s.get("ok") for s in out.get("services", {}).values())
        if not ok:
            print(
                "\nIf the endpoint is unreachable or returns HTML: check your connection/VPN and the base URL. "
                "This tool never tries to bypass network restrictions.",
                file=sys.stderr,
            )
        return 0 if ok else 1
    finally:
        await res.aclose()
        await res.store.close()


def cmd_doctor(a: argparse.Namespace) -> int:
    return asyncio.run(_doctor(a))


async def _capabilities(a: argparse.Namespace) -> int:
    from .bootstrap import local_resolver
    from .domain.capabilities import matrix

    res = local_resolver()
    await res.store.init()
    try:
        acct = await res.account(a.profile)
        _print(await matrix(acct, detect=not a.offline), a.json)
    finally:
        await res.aclose()
        await res.store.close()
    return 0


def cmd_capabilities(a: argparse.Namespace) -> int:
    return asyncio.run(_capabilities(a))


# ------------------------------------------------------------------------------------------ serve
def cmd_serve(a: argparse.Namespace) -> int:
    setup_logging(os.environ.get("MAMCP_LOG_LEVEL", "INFO"))
    if a.transport == "stdio":
        from .bootstrap import local_resolver
        from .server.mcp_app import build_mcp

        mcp = build_mcp(local_resolver(), manage_store=True)
        mcp.run(transport="stdio")
        return 0
    from .server.http_app import run_http

    run_http(host=a.host, port=a.port)
    return 0


def cmd_version(a: argparse.Namespace) -> int:
    print(f"moodle-academic-mcp {__version__}")
    return 0


# ------------------------------------------------------------------------------------------ admin helpers
async def _admin_dev_token(a: argparse.Namespace) -> int:
    from .bootstrap import hosted_resolver

    cfg = AppConfig.load(a.config) if a.config else AppConfig.load()
    res = hosted_resolver(cfg)
    await res.store.init()
    token = "mamcp_dev_" + secrets.token_urlsafe(32)
    user_key = f"dev|{a.user}"
    await res.store.ensure_user(user_key, a.user)
    await res.store.put_dev_token(
        hashlib.sha256(token.encode()).hexdigest(), user_key, int(time.time()) + a.days * 86400
    )
    print(token)
    print(
        f"# user_key={user_key} valid {a.days} days. Dev auth only; use OIDC in production.", file=sys.stderr
    )
    await res.store.close()
    return 0


async def _admin_data(a: argparse.Namespace) -> int:
    from .bootstrap import hosted_resolver, local_resolver

    if a.hosted:
        res = hosted_resolver(AppConfig.load())
        user_key = a.user
    else:
        res = local_resolver()
        user_key = "local"
    await res.store.init()
    try:
        if a.action == "export":
            print(json.dumps(await res.store.export_user(user_key), indent=2, default=str))
        else:
            if not a.yes:
                print("Refusing to delete without --yes", file=sys.stderr)
                return 2
            counts = await res.store.purge_user(user_key, a.profile)
            if not a.hosted:
                tokens = LocalTokenStore()
                for prof in [a.profile] if a.profile else list(AppConfig.load().profiles):
                    for s in tokens.services(prof):
                        tokens.delete(prof, s)
            print(json.dumps(counts))
    finally:
        await res.store.close()
    return 0


def cmd_functions(a: argparse.Namespace) -> int:
    from .moodle.catalog import load_catalog

    cat = load_catalog()
    rows = cat.search(a.query, a.limit) if a.query else list(cat.functions.values())[: a.limit]
    for f in rows:
        print(f"{f.effect.value:5} {f.name}  [{f.source}]  {f.description[:70]}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        "moodle-academic-mcp", description="Community MCP server for Moodle (student side)."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("version")
    s.set_defaults(fn=cmd_version)

    s = sub.add_parser("configure", help="create/update a profile and store tokens (hidden input)")
    s.add_argument("--profile")
    s.add_argument("--url")
    s.add_argument("--timezone")
    s.add_argument(
        "--service", action="append", help="service token to enter (repeatable): mobile, attendance, vpl"
    )
    s.add_argument("--allow-insecure-http", action="store_true", help="development only")
    s.add_argument(
        "--allow-private-networks", action="store_true", help="self-hosted Moodle on a private network"
    )
    s.set_defaults(fn=cmd_configure)

    s = sub.add_parser("doctor", help="check connection, tokens, identity and features")
    s.add_argument("--profile")
    s.add_argument("--json", action="store_true")
    s.add_argument("--no-detect", action="store_true")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("capabilities", help="feature x state matrix")
    s.add_argument("--profile")
    s.add_argument("--json", action="store_true")
    s.add_argument("--offline", action="store_true", help="do not call Moodle")
    s.set_defaults(fn=cmd_capabilities)

    s = sub.add_parser("serve", help="run the MCP server")
    s.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8080)
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("functions", help="search the bundled function catalog (offline)")
    s.add_argument("query", nargs="?", default="")
    s.add_argument("--limit", type=int, default=30)
    s.set_defaults(fn=cmd_functions)

    s = sub.add_parser("dev-token", help="hosted dev auth: create a bearer token for a user")
    s.add_argument("user")
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--config")
    s.set_defaults(fn=lambda a: asyncio.run(_admin_dev_token(a)))

    s = sub.add_parser("data", help="export or delete the data this integration stores")
    s.add_argument("action", choices=["export", "purge"])
    s.add_argument("--hosted", action="store_true")
    s.add_argument("--user", default="local")
    s.add_argument("--profile")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=lambda a: asyncio.run(_admin_data(a)))
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args) or 0)
    except MamcpError as e:
        print(json.dumps(scrub(e.to_dict()), ensure_ascii=False), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
