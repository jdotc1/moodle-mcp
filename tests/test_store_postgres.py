"""Runs only with MAMCP_TEST_POSTGRES_URL (CI service or `docker run postgres`). Real PostgreSQL, no mocks."""

import os

import pytest

from moodle_academic_mcp.store import Store
from moodle_academic_mcp.vault import Vault

URL = os.environ.get("MAMCP_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="MAMCP_TEST_POSTGRES_URL not set")


async def test_store_roundtrip_isolation_and_purge_on_postgres():
    s = Store(URL)
    await s.init()
    try:
        v = Vault("kbbxXsmaNuhYPBXpBRuS0i9Gmg1UjkLV9h-5O7E7Wdc=")
        for u in ("pg-alice", "pg-bob"):
            await s.purge_user(u)
            await s.ensure_user(u)
        await s.put_credential("pg-alice", "ufsc", "mobile", v.seal("pg-alice", "ufsc", "mobile", "tok-a"))
        ct = await s.get_credential("pg-alice", "ufsc", "mobile")
        assert v.open("pg-alice", "ufsc", "mobile", ct) == "tok-a"
        assert v.open("pg-bob", "ufsc", "mobile", ct) is None  # row swapping defeated
        assert await s.get_credential("pg-bob", "ufsc", "mobile") is None
        await s.replace_chunks("pg-alice", "ufsc", 1, "mod:1:2:0", [("page 1", "texto")], "https://m/x")
        assert not await s.get_chunks("pg-bob", "ufsc")
        op_id, prior = await s.begin_operation("pg-alice", "ufsc", "save_submission", "1", "h")
        assert prior is None
        await s.finish_operation(op_id, "done", {"ok": True})
        _, prior2 = await s.begin_operation("pg-alice", "ufsc", "save_submission", "1", "h")
        assert prior2 and prior2["status"] == "done"
        counts = await s.purge_user("pg-alice")
        assert counts["credentials"] == 1
    finally:
        await s.close()
