"""Wiring for both run modes (same core)."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from .config import AppConfig, LocalTokenStore, data_dir
from .runtime import Runtime
from .server.resolver import DbCredentialSource, LocalCredentialSource, Resolver
from .store import Store
from .vault import Vault


def local_resolver(
    config: AppConfig | None = None,
    *,
    store: Store | None = None,
    http: httpx.AsyncClient | None = None,
    tokens: LocalTokenStore | None = None,
    runtime: Runtime | None = None,
) -> Resolver:
    config = config or AppConfig.load()
    store = store or Store.sqlite(Path(os.environ.get("MAMCP_STATE_DB", data_dir() / "state.sqlite3")))
    return Resolver(
        config,
        store,
        runtime or Runtime(mode="local"),
        LocalCredentialSource(tokens or LocalTokenStore()),
        http=http,
        hosted=False,
    )


def hosted_resolver(
    config: AppConfig,
    *,
    database_url: str | None = None,
    vault: Vault | None = None,
    http: httpx.AsyncClient | None = None,
    runtime: Runtime | None = None,
    store: Store | None = None,
) -> Resolver:
    url = database_url or os.environ.get("MAMCP_DATABASE_URL", "sqlite+aiosqlite:///./mamcp-hosted.sqlite3")
    store = store or Store(url)
    rt = runtime or Runtime(
        mode="remote", artifact_dir=Path(os.environ.get("MAMCP_ARTIFACT_DIR", "./artifacts"))
    )
    return Resolver(
        config, store, rt, DbCredentialSource(store, vault or Vault.from_env()), http=http, hosted=True
    )


@asynccontextmanager
async def lifecycle(resolver: Resolver):  # type: ignore[no-untyped-def]
    await resolver.store.init()
    try:
        yield resolver
    finally:
        await resolver.aclose()
        await resolver.store.close()
