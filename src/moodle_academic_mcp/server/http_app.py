"""Hosted mode: FastAPI (health, onboarding, OpenAPI for GPT Actions, artifacts) + MCP Streamable HTTP at /mcp."""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl, BaseModel, create_model

from .. import __version__
from ..bootstrap import hosted_resolver
from ..config import AppConfig
from ..domain.doctor import diagnose
from ..domain.files import cleanup_artifacts, save_artifact
from ..errors import AuthError, MamcpError
from ..moodle.client import MoodleClient
from ..redact import scrub, setup_logging
from . import ops  # noqa: F401
from .auth import DevTokenVerifier, OIDCVerifier
from .mcp_app import build_mcp
from .registry import OPS
from .resolver import Resolver, reset_current_user, set_current_user

log = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)

# Curated subset for GPT Actions (the builder limits the number of operations per spec).
ACTIONS_OPS = [
    "moodle_status",
    "list_courses",
    "course_contents",
    "list_deadlines",
    "list_assignments",
    "get_assignment",
    "submission_status",
    "prepare_submission",
    "deliver_assignment",
    "save_submission",
    "submit_assignment",
    "list_quizzes",
    "quiz_info",
    "list_forums",
    "list_discussions",
    "get_discussion",
    "changes_since",
    "get_grades",
    "attendance_report",
    "list_materials",
    "read_material",
    "search_materials",
    "my_groups",
    "group_activities",
    "join_group",
    "create_artifact",
    "simulate_grades",
    "required_score",
]
HTTP_STATUS = {
    "unauthenticated": 401,
    "permission_denied": 403,
    "not_found": 404,
    "invalid_input": 422,
    "token_invalid": 424,
    "not_configured": 409,
    "function_unavailable": 501,
    "unsupported": 501,
    "network_restriction": 503,
    "service_unavailable": 503,
    "unsafe_destination": 400,
    "write_outcome_unknown": 502,
}


class TokenBody(BaseModel):
    token: str
    store_anyway: bool = False


class RateLimiter:
    def __init__(self, limit: int, per_seconds: int):
        self.limit, self.per = limit, per_seconds
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > self.per:
            q.popleft()
        if len(q) >= self.limit:
            raise HTTPException(429, "Too many requests", headers={"Retry-After": str(self.per)})
        q.append(now)


def create_app(
    resolver: Resolver,
    verifier: Any,
    *,
    public_url: str | None = None,
    oidc_public: dict | None = None,
    auth_settings: AuthSettings | None = None,
) -> FastAPI:
    host = urlsplit(public_url).netloc if public_url else None
    if auth_settings is None:  # dev default (localhost); production passes explicit settings
        root = public_url or "http://127.0.0.1"
        auth_settings = AuthSettings(
            issuer_url=AnyHttpUrl(root), resource_server_url=AnyHttpUrl(f"{root}/mcp")
        )
    mcp = build_mcp(resolver, token_verifier=verifier, auth=auth_settings, public_host=host)
    mcp_app = mcp.streamable_http_app()
    limiter = RateLimiter(30, 60)

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        await resolver.store.init()
        resolver.runtime.artifact_dir.mkdir(parents=True, exist_ok=True)
        async with mcp.session_manager.run():
            yield
        await resolver.aclose()
        await resolver.store.close()

    app = FastAPI(
        title="moodle-academic-mcp",
        version=__version__,
        lifespan=lifespan,
        description="Community API over the same operations as the MCP tools (GPT Actions friendly).",
        servers=[{"url": public_url}] if public_url else None,
    )

    async def user(request: Request, cred: HTTPAuthorizationCredentials | None = Depends(bearer)):  # type: ignore[no-untyped-def]
        if cred is None:
            raise HTTPException(401, "Missing bearer token", headers={"WWW-Authenticate": "Bearer"})
        at = await verifier.verify_token(cred.credentials)
        if at is None:
            raise HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
        tok = set_current_user(at.client_id)
        request.state.reset = tok
        try:
            yield at.client_id
        finally:
            reset_current_user(tok)

    @app.exception_handler(MamcpError)
    async def _mamcp(_: Request, e: MamcpError):  # type: ignore[no-untyped-def]
        return JSONResponse(scrub(e.to_dict()), status_code=HTTP_STATUS.get(e.code, 400))

    # ------------------------------------------------------------------ public
    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/ready", tags=["meta"])
    async def ready() -> dict:
        from sqlalchemy import text

        async with resolver.store.engine.connect() as c:
            await c.execute(text("select 1"))
        return {"status": "ready"}

    @app.get("/api/v1/profiles", tags=["onboarding"])
    async def profiles() -> dict:
        return {"profiles": [p.to_public() for p in resolver.config.profiles.values()]}

    @app.get("/onboarding", response_class=HTMLResponse, tags=["onboarding"], include_in_schema=False)
    async def onboarding() -> str:
        return (
            resources.files("moodle_academic_mcp.server")
            .joinpath("static/onboarding.html")
            .read_text("utf-8")
        )

    @app.get("/onboarding/config", include_in_schema=False)
    async def onboarding_config() -> dict:
        return {"oidc": oidc_public}

    # ------------------------------------------------------------------ onboarding (authenticated)
    @app.get("/api/v1/me/connections", tags=["onboarding"])
    async def connections(uk: str = Depends(user)) -> dict:
        rows = await resolver.store.list_credentials(uk)
        return {
            "user": uk.split("|")[-1],
            "connections": [
                {"profile": r["profile"], "service": r["service"], "updated_at": r["updated_at"]}
                for r in rows
            ],
        }

    @app.put("/api/v1/profiles/{profile}/tokens/{service}", tags=["onboarding"])
    async def put_token(profile: str, service: str, body: TokenBody, uk: str = Depends(user)) -> dict:
        limiter.check(f"tok:{uk}")
        prof = resolver.config.profile(profile)
        token = body.token.strip()
        if not (4 <= len(token) <= 256) or any(c.isspace() for c in token) or not service.isidentifier():
            raise HTTPException(422, detail={"message": "That does not look like a Moodle token."})
        client = MoodleClient(prof.endpoint(), token, service, http=resolver.http, read_retries=1)
        test: dict[str, Any]
        try:
            info = await client.call("core_webservice_get_site_info")
            test = {
                "ok": True,
                "moodle_release": info.get("release"),
                "functions": len(info.get("functions", [])),
            }
        except MamcpError as e:
            test = {"ok": False, **scrub(e.to_dict())}
        finally:
            await client.aclose()
        if not test["ok"] and not body.store_anyway:
            raise HTTPException(422, detail=test)
        await resolver.creds.put(uk, prof.name, service, token)  # type: ignore[union-attr]
        await resolver.forget(uk, prof.name)
        return {"stored": True, "profile": prof.name, "service": service, "test": test}

    @app.delete("/api/v1/profiles/{profile}/tokens/{service}", tags=["onboarding"])
    async def del_token(profile: str, service: str, uk: str = Depends(user)) -> dict:
        await resolver.creds.delete(uk, profile, service)  # type: ignore[union-attr]
        await resolver.forget(uk, profile)
        return {"deleted": True}

    @app.post("/api/v1/profiles/{profile}/test", tags=["onboarding"])
    async def test_conn(profile: str, uk: str = Depends(user)) -> dict:
        return await diagnose(await resolver.account(profile))

    @app.get("/api/v1/me/export", tags=["privacy"])
    async def export(uk: str = Depends(user)) -> dict:
        return scrub(await resolver.store.export_user(uk))

    @app.delete("/api/v1/me/data", tags=["privacy"])
    async def purge(uk: str = Depends(user)) -> dict:
        counts = await resolver.store.purge_user(uk)
        await resolver.forget(uk)
        for a in await resolver.store.user_artifacts(uk):  # pragma: no cover - already deleted rows
            Path(a["path"]).unlink(missing_ok=True)
        return {"deleted": counts}

    # ------------------------------------------------------------------ artifacts
    @app.post("/api/v1/artifacts", tags=["files"], operation_id="upload_artifact")
    async def upload_artifact(file: UploadFile = File(...), uk: str = Depends(user)) -> dict:
        limiter.check(f"up:{uk}")
        acct = await _any_account(uk)
        data = await file.read(resolver.runtime.max_upload_bytes + 1)
        return await save_artifact(acct, file.filename or "upload.bin", data)

    async def _any_account(uk: str):  # type: ignore[no-untyped-def]
        from ..domain.account import Account

        prof = next(iter(resolver.config.profiles.values()))
        return Account(user_key=uk, profile=prof, tokens={}, store=resolver.store, runtime=resolver.runtime)

    @app.get("/api/v1/artifacts", tags=["files"])
    async def list_artifacts(uk: str = Depends(user)) -> dict:
        rows = await resolver.store.user_artifacts(uk)
        return {
            "artifacts": [
                {
                    "artifact": f"artifact:{r['id']}",
                    "filename": r["filename"],
                    "size": r["size"],
                    "expires_at": r["expires_at"],
                }
                for r in rows
            ]
        }

    # ------------------------------------------------------------------ operations as REST (same registry)
    for o in OPS.values():
        sig = o.public_signature()
        fields: dict[str, Any] = {}
        for p in sig.parameters.values():
            fields[p.name] = (p.annotation, ... if p.default is p.empty else p.default)
        Model = create_model(f"{o.name}_request", **fields)  # type: ignore[call-overload]

        def make(o=o, Model=Model):  # type: ignore[no-untyped-def]
            async def handler(body: Model, uk: str = Depends(user)) -> dict:  # type: ignore[valid-type]
                kwargs = body.model_dump()  # type: ignore[attr-defined]
                profile = kwargs.pop("profile", None)
                acct = await resolver.account(profile)
                return scrub(await o.fn(acct, **kwargs))

            handler.__name__ = o.name
            handler.__annotations__ = {"body": Model, "uk": str, "return": dict}  # real objects, not strings
            return handler

        app.post(
            f"/api/v1/tools/{o.name}",
            operation_id=o.name,
            summary=o.name.replace("_", " "),
            description=o.description,
            tags=o.tags or ["tools"],  # type: ignore[arg-type]
        )(make())

    @app.get("/openapi-actions.json", include_in_schema=False)
    async def openapi_actions() -> dict:
        full = app.openapi()
        keep = {f"/api/v1/tools/{n}" for n in ACTIONS_OPS} | {"/api/v1/artifacts"}
        spec = {
            **full,
            "paths": {p: v for p, v in full["paths"].items() if p in keep and not (p == "/api/v1/artifacts")},
        }
        return spec

    base_openapi = app.openapi

    def custom_openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = base_openapi()
        issuer = os.environ.get("MAMCP_OIDC_ISSUER")
        if oidc_public:
            schema.setdefault("components", {}).setdefault("securitySchemes", {})["oauth2"] = {
                "type": "oauth2",
                "flows": {
                    "authorizationCode": {
                        "authorizationUrl": oidc_public["authorization_endpoint"],
                        "tokenUrl": oidc_public["token_endpoint"],
                        "scopes": {
                            oidc_public.get("scope", "openid"): "Access your Moodle data through this server"
                        },
                    }
                },
            }
        elif issuer is None:
            schema.setdefault("components", {}).setdefault("securitySchemes", {})["bearer"] = {
                "type": "http",
                "scheme": "bearer",
            }
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]

    @app.middleware("http")
    async def cleanup_mw(request: Request, call_next):  # type: ignore[no-untyped-def]
        if int(time.time()) % 50 == 0:
            try:
                await cleanup_artifacts(resolver.store)
            except Exception:  # pragma: no cover
                log.exception("artifact cleanup failed")
        return await call_next(request)

    app.mount("/", mcp_app)  # /mcp (Streamable HTTP) and /.well-known/oauth-protected-resource/...
    return app


def _load_config() -> AppConfig:
    path = os.environ.get("MAMCP_PROFILES_FILE")
    if not path:
        raise AuthError(
            "Set MAMCP_PROFILES_FILE to a config.toml listing the institutions this server serves."
        )
    return AppConfig.load(Path(path))


def build_from_env(http: httpx.AsyncClient | None = None) -> FastAPI:
    setup_logging(os.environ.get("MAMCP_LOG_LEVEL", "INFO"))
    config = _load_config()
    resolver = hosted_resolver(config, http=http)
    public_url = os.environ.get("MAMCP_PUBLIC_URL")
    mode = os.environ.get("MAMCP_AUTH_MODE", "oidc")
    oidc_public = None
    auth_settings = None
    if mode == "dev":
        if not os.environ.get("MAMCP_ALLOW_DEV_AUTH"):
            raise AuthError("Dev auth is for local development: set MAMCP_ALLOW_DEV_AUTH=1 to acknowledge.")
        verifier: Any = DevTokenVerifier(resolver.store)
        if public_url:
            auth_settings = AuthSettings(
                issuer_url=AnyHttpUrl(public_url), resource_server_url=AnyHttpUrl(f"{public_url}/mcp")
            )
    else:
        issuer = os.environ["MAMCP_OIDC_ISSUER"]
        audience = os.environ.get("MAMCP_OIDC_AUDIENCE") or (f"{public_url}/mcp" if public_url else "")
        verifier = OIDCVerifier(
            issuer,
            audience,
            os.environ.get("MAMCP_OIDC_JWKS_URL"),
            required_scope=os.environ.get("MAMCP_OIDC_REQUIRED_SCOPE"),
        )
        auth_settings = AuthSettings(
            issuer_url=AnyHttpUrl(issuer),
            resource_server_url=AnyHttpUrl(f"{public_url}/mcp"),
            required_scopes=None,
            validate_token_resource=bool(audience),
        )
        if os.environ.get("MAMCP_ONBOARDING_CLIENT_ID"):
            oidc_public = {
                "client_id": os.environ["MAMCP_ONBOARDING_CLIENT_ID"],
                "authorization_endpoint": os.environ["MAMCP_OIDC_AUTHORIZE_URL"],
                "token_endpoint": os.environ["MAMCP_OIDC_TOKEN_URL"],
                "scope": os.environ.get("MAMCP_OIDC_SCOPE", "openid profile"),
                "audience": audience,
            }
    return create_app(
        resolver, verifier, public_url=public_url, oidc_public=oidc_public, auth_settings=auth_settings
    )


def run_http(host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(build_from_env(), host=host, port=port, log_level="info", proxy_headers=True)
