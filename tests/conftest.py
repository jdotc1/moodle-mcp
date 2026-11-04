from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import uvicorn

from moodle_academic_mcp.config import ProfileSettings
from moodle_academic_mcp.domain.account import Account
from moodle_academic_mcp.runtime import Runtime
from moodle_academic_mcp.store import Store
from tests.fake_moodle import State, create_fake


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerThread:
    def __init__(self, app, port: int):
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("server did not start")

    def __exit__(self, *a):
        self.server.should_exit = True
        self.thread.join(timeout=5)


class LoseResponseOnce(httpx.AsyncBaseTransport):
    """Delegates to the fake app (so the write IS applied), then raises ReadTimeout once for a given function."""

    def __init__(self, inner: httpx.AsyncBaseTransport, fn_name: str):
        self.inner, self.fn_name, self.armed = inner, fn_name, True

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = (await request.aread()).decode(errors="ignore")
        resp = await self.inner.handle_async_request(request)
        if self.armed and f"wsfunction={self.fn_name}" in body:
            self.armed = False
            raise httpx.ReadTimeout("simulated lost response", request=request)
        return resp


@pytest.fixture
def fake():
    app, state = create_fake()
    return app, state


@pytest.fixture
def fake_state(fake) -> State:
    return fake[1]


@pytest_asyncio.fixture
async def http(fake):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake[0]), base_url="http://fake", timeout=1.0
    ) as c:
        yield c


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    s = Store.sqlite(tmp_path / "state.sqlite3")
    await s.init()
    yield s
    await s.close()


def profile(name: str = "fake", base: str = "http://fake") -> ProfileSettings:
    return ProfileSettings(
        name=name,
        base_url=base,
        institution=name,
        timezone="America/Sao_Paulo",
        allow_insecure_http=True,
        allow_private_networks=True,
    )


@pytest.fixture
def make_account(http, store, tmp_path):
    made = []

    def _make(user_key="u-alice", tokens=None, prof=None, runtime=None) -> Account:
        a = Account(
            user_key=user_key,
            profile=prof or profile(),
            tokens=tokens or {"mobile": "tokA"},
            store=store,
            http=http,
            runtime=runtime or Runtime(mode="local", upload_roots=[tmp_path]),
        )
        made.append(a)
        return a

    return _make


@pytest.fixture
def alice(make_account):
    return make_account()
