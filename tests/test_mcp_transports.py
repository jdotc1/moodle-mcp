"""Real MCP handshake/discovery/calls: stdio (subprocess) and Streamable HTTP (uvicorn), against the MOCK Moodle."""

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from moodle_academic_mcp.bootstrap import hosted_resolver
from moodle_academic_mcp.config import AppConfig
from moodle_academic_mcp.server.auth import DevTokenVerifier
from moodle_academic_mcp.server.http_app import ACTIONS_OPS, create_app
from moodle_academic_mcp.server.registry import OPS
from moodle_academic_mcp.store import Store
from moodle_academic_mcp.vault import Vault
from tests.conftest import ServerThread, free_port
from tests.fake_moodle import create_fake

CONFIG = """default_profile = "fake"

[profiles.fake]
base_url = "http://127.0.0.1:{port}"
institution = "Fake"
timezone = "America/Sao_Paulo"
allow_insecure_http = true
allow_private_networks = true
"""


def payload(res):
    assert res.content, res
    return json.loads(res.content[0].text)


@pytest.fixture
def moodle():
    app, state = create_fake()
    port = free_port()
    with ServerThread(app, port):
        yield port, state


async def test_stdio_handshake_discovery_and_calls(moodle, tmp_path):
    port, state = moodle
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "config.toml").write_text(CONFIG.format(port=port))
    work = tmp_path / "work"
    work.mkdir()
    (work / "entrega.txt").write_text("meu trabalho")
    env = {
        **os.environ,
        "MAMCP_CONFIG_DIR": str(cfg),
        "MAMCP_DATA_DIR": str(tmp_path / "data"),
        "MAMCP_NO_KEYRING": "1",
        "MOODLE_TOKEN_FAKE_MOBILE": "tokA",
        "MAMCP_UPLOAD_ROOTS": str(work),
        "MAMCP_LOG_LEVEL": "WARNING",
    }
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "moodle_academic_mcp.cli", "serve"], env=env
    )
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        init = await s.initialize()
        assert init.serverInfo.name == "moodle-academic-mcp" and "untrusted" in init.instructions
        tools = {t.name: t for t in (await s.list_tools()).tools}
        assert set(OPS) == set(tools)
        assert tools["list_courses"].annotations.readOnlyHint is True
        d = tools["deliver_assignment"]
        assert d.annotations.readOnlyHint is False and d.annotations.destructiveHint is True
        assert "assignment_id" in d.inputSchema["properties"] and "profile" in d.inputSchema["properties"]
        assert d.inputSchema["required"] == ["assignment_id"]
        assert (
            tools["moodle_call_read"].annotations.readOnlyHint is True
            and tools["moodle_call_write"].annotations.readOnlyHint is False
        )
        prompts = {p.name for p in (await s.list_prompts()).prompts}
        assert {"prepare_assignment", "deliver_assignment_workflow", "attendance_check"} <= prompts
        pr = await s.get_prompt("prepare_assignment", {"assignment": "Trabalho 1"})
        assert "get_assignment" in pr.messages[0].content.text and "untrusted" in pr.messages[0].content.text

        courses = await s.call_tool("list_courses", {})
        assert not courses.isError and courses.structuredContent["items"][0]["id"] == 10
        dl = await s.call_tool("list_deadlines", {"range": "next_week"})
        assert dl.structuredContent["window"]["label"].startswith("next_week")
        # the composite write, via the real protocol
        out = await s.call_tool(
            "deliver_assignment",
            {"assignment_id": 1, "files": [str(work / "entrega.txt")], "accept_statement": True},
        )
        assert out.structuredContent["delivered"] is True
        assert state.subs[(101, 1)]["status"] == "submitted"
        # errors are structured, not stack traces
        bad = await s.call_tool("submission_status", {"assignment_id": 999})
        assert bad.isError
        outside = await s.call_tool("save_submission", {"assignment_id": 1, "files": ["/etc/passwd"]})
        assert outside.isError and "unsafe_destination" in outside.content[0].text
        assert "tokA" not in json.dumps([c.text for c in outside.content])


# ============================================================================ hosted / Streamable HTTP
@pytest.fixture
def hosted(moodle, tmp_path):
    mport, state = moodle
    cfgfile = tmp_path / "profiles.toml"
    cfgfile.write_text(CONFIG.format(port=mport))
    db = f"sqlite+aiosqlite:///{tmp_path}/hosted.sqlite3"
    key = Fernet.generate_key().decode()
    resolver = hosted_resolver(AppConfig.load(cfgfile), database_url=db, vault=Vault(key), runtime=None)
    resolver.runtime.artifact_dir = tmp_path / "artifacts"

    async def mint():
        s = Store(db)
        await s.init()
        import hashlib
        import time

        for name in ("alice", "bob", "carol"):
            await s.ensure_user(f"dev|{name}")
            await s.put_dev_token(
                hashlib.sha256(f"mamcp_dev_{name}".encode()).hexdigest(),
                f"dev|{name}",
                int(time.time()) + 3600,
            )
        await s.close()

    asyncio.run(mint())
    app = create_app(resolver, DevTokenVerifier(resolver.store))
    port = free_port()
    with ServerThread(app, port):
        yield port, state, db, tmp_path


def H(name):
    return {"Authorization": f"Bearer mamcp_dev_{name}"}


async def mcp_call(port, who, tool, args=None):
    async with (
        streamablehttp_client(f"http://127.0.0.1:{port}/mcp", headers=H(who)) as (r, w, _),
        ClientSession(r, w) as s,
    ):
        await s.initialize()
        return await s.call_tool(tool, args or {})


async def test_http_auth_onboarding_isolation_and_artifacts(hosted):
    port, state, db, tmp_path = hosted
    base = f"http://127.0.0.1:{port}"
    async with httpx.AsyncClient() as c:
        assert (await c.get(f"{base}/health")).json()["status"] == "ok"
        assert (await c.post(f"{base}/mcp", json={})).status_code == 401  # no bearer
        assert (
            await c.post(f"{base}/mcp", json={}, headers={"Authorization": "Bearer nope"})
        ).status_code == 401
        assert (await c.get(f"{base}/api/v1/me/connections")).status_code == 401
        assert (await c.get(f"{base}/onboarding")).status_code == 200
        # onboarding: a bad Moodle token is rejected, good ones are stored (encrypted)
        bad = await c.put(
            f"{base}/api/v1/profiles/fake/tokens/mobile", json={"token": "wrongtoken123"}, headers=H("alice")
        )
        assert bad.status_code == 422
        for who, tok in (("alice", "tokA"), ("bob", "tokB")):
            ok = await c.put(
                f"{base}/api/v1/profiles/fake/tokens/mobile", json={"token": tok}, headers=H(who)
            )
            assert ok.status_code == 200 and ok.json()["test"]["ok"] and tok not in ok.text
        conns = (await c.get(f"{base}/api/v1/me/connections", headers=H("alice"))).json()
        assert [x["service"] for x in conns["connections"]] == ["mobile"]

    raw = Path(db.split("///")[1]).read_bytes()
    assert b"tokA" not in raw and b"tokB" not in raw  # ciphertext at rest

    # same endpoint, two identities -> each sees only their own Moodle account
    a = payload(await mcp_call(port, "alice", "moodle_status", {"check_functions": False}))
    b = payload(await mcp_call(port, "bob", "moodle_status", {"check_functions": False}))
    assert a["services"]["mobile"]["user_id"] == 101 and b["services"]["mobile"]["user_id"] == 102
    assert "userprivateaccesskey" not in json.dumps(a) and "SECRETKEY" not in json.dumps(a)
    # a user without stored credentials is told how to onboard, not given someone else's data
    r = await mcp_call(port, "carol", "list_courses")
    assert r.isError and "not_configured" in r.content[0].text

    # remote artifacts: authenticated upload, id-based delivery, no local paths, no cross-user use
    async with httpx.AsyncClient() as c:
        up = await c.post(
            f"{base}/api/v1/artifacts",
            files={"file": ("../../evil name.txt", b"conteudo remoto")},
            headers=H("alice"),
        )
        art = up.json()
        assert up.status_code == 200 and "/" not in art["filename"] and art["filename"].endswith(".txt")
    local = await mcp_call(port, "alice", "save_submission", {"assignment_id": 1, "files": ["/etc/passwd"]})
    assert local.isError and "not available in remote mode" in local.content[0].text
    stolen = await mcp_call(port, "bob", "save_submission", {"assignment_id": 1, "files": [art["artifact"]]})
    assert stolen.isError and "not_found" in stolen.content[0].text
    ok = await mcp_call(
        port,
        "alice",
        "deliver_assignment",
        {"assignment_id": 1, "files": [art["artifact"]], "accept_statement": True},
    )
    assert ok.structuredContent["delivered"] is True
    assert list(state.subs[(101, 1)]["files"]) == [art["filename"]] and (102, 1) not in state.subs
    created = payload(
        await mcp_call(port, "alice", "create_artifact", {"filename": "t.txt", "content": "ola"})
    )
    assert created["artifact"].startswith("artifact:")

    # REST/OpenAPI facade shares the same operations (for GPT Actions)
    async with httpx.AsyncClient() as c:
        spec = (await c.get(f"{base}/openapi.json")).json()
        op_ids = {o["operationId"] for p in spec["paths"].values() for o in p.values() if "operationId" in o}
        assert set(OPS) <= op_ids
        actions = (await c.get(f"{base}/openapi-actions.json")).json()
        assert 0 < len(actions["paths"]) <= 30 and set(ACTIONS_OPS) >= {
            p.rsplit("/", 1)[-1] for p in actions["paths"]
        }
        res = await c.post(f"{base}/api/v1/tools/list_courses", json={}, headers=H("bob"))
        assert res.status_code == 200 and res.json()["items"][0]["id"] == 10
        assert (await c.post(f"{base}/api/v1/tools/list_courses", json={})).status_code == 401
        e = await c.post(f"{base}/api/v1/tools/list_courses", json={}, headers=H("carol"))
        assert e.status_code == 409 and e.json()["error"] == "not_configured"
        # privacy: export (no secrets) and purge
        exp = (await c.get(f"{base}/api/v1/me/export", headers=H("alice"))).json()
        assert "tokA" not in json.dumps(exp) and exp["credentials"][0]["service"] == "mobile"
        assert (await c.delete(f"{base}/api/v1/me/data", headers=H("alice"))).status_code == 200
        gone = await c.post(f"{base}/api/v1/tools/list_courses", json={}, headers=H("alice"))
        assert gone.status_code in (401, 409)  # purge also revoked her dev token / credentials


async def test_http_revoked_moodle_token_surfaces_as_tool_error(hosted):
    port, state, *_ = hosted
    async with httpx.AsyncClient() as c:
        await c.put(
            f"http://127.0.0.1:{port}/api/v1/profiles/fake/tokens/mobile",
            json={"token": "tokA"},
            headers=H("alice"),
        )
    state.revoked.add("tokA")
    r = await mcp_call(port, "alice", "list_courses")
    assert r.isError and "token_invalid" in r.content[0].text
