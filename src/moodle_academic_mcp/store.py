"""Persistence (SQLite locally, PostgreSQL when hosted) through SQLAlchemy Core.

Every query is scoped by (user_key, profile): isolation is enforced here, never by model-supplied ids.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

meta = MetaData()

users = Table(
    "users",
    meta,
    Column("user_key", String(255), primary_key=True),
    Column("label", String(255)),
    Column("created_at", BigInteger),
)
credentials = Table(
    "credentials",
    meta,
    Column("user_key", String(255), primary_key=True),
    Column("profile", String(64), primary_key=True),
    Column("service", String(64), primary_key=True),
    Column("ciphertext", LargeBinary),
    Column("updated_at", BigInteger),
)
identity_links = Table(
    "identity_links",
    meta,
    Column("user_key", String(255), primary_key=True),
    Column("profile", String(64), primary_key=True),
    Column("service", String(64), primary_key=True),
    Column("moodle_userid", BigInteger),
    Column("verified", Boolean),
    Column("evidence", Text),
    Column("checked_at", BigInteger),
)
dev_tokens = Table(
    "dev_tokens",
    meta,
    Column("token_hash", String(64), primary_key=True),
    Column("user_key", String(255)),
    Column("expires_at", BigInteger),
    Column("revoked", Boolean, default=False),
)
snapshots = Table(
    "snapshots",
    meta,
    Column("user_key", String(255), primary_key=True),
    Column("profile", String(64), primary_key=True),
    Column("skey", String(255), primary_key=True),
    Column("payload", Text),
    Column("taken_at", BigInteger),
)
operations = Table(
    "operations",
    meta,
    Column("op_id", String(64), primary_key=True),
    Column("user_key", String(255)),
    Column("profile", String(64)),
    Column("kind", String(64)),
    Column("target", String(255)),
    Column("payload_hash", String(64)),
    Column("status", String(32)),
    Column("evidence", Text),
    Column("created_at", BigInteger),
    Column("updated_at", BigInteger),
)
artifacts = Table(
    "artifacts",
    meta,
    Column("id", String(64), primary_key=True),
    Column("user_key", String(255)),
    Column("filename", String(255)),
    Column("size", BigInteger),
    Column("sha256", String(64)),
    Column("path", Text),
    Column("created_at", BigInteger),
    Column("expires_at", BigInteger),
)
manual_attendance = Table(
    "manual_attendance",
    meta,
    Column("user_key", String(255), primary_key=True),
    Column("profile", String(64), primary_key=True),
    Column("course_id", Integer, primary_key=True),
    Column("payload", Text),
    Column("updated_at", BigInteger),
)
material_chunks = Table(
    "material_chunks",
    meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_key", String(255), index=True),
    Column("profile", String(64)),
    Column("course_id", Integer),
    Column("ref", String(512)),
    Column("location", String(64)),
    Column("text", Text),
    Column("source_url", Text),
    Column("indexed_at", BigInteger),
)


def now() -> int:
    return int(time.time())


class Store:
    def __init__(self, url: str):
        self.url = url
        self.engine: AsyncEngine = create_async_engine(url, future=True)

    @classmethod
    def sqlite(cls, path: Path) -> Store:
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(f"sqlite+aiosqlite:///{path}")

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(meta.create_all)

    async def close(self) -> None:
        await self.engine.dispose()

    # ------------------------------------------------------------- users / credentials
    async def ensure_user(self, user_key: str, label: str = "") -> None:
        async with self.engine.begin() as c:
            row = (await c.execute(select(users.c.user_key).where(users.c.user_key == user_key))).first()
            if not row:
                await c.execute(insert(users).values(user_key=user_key, label=label, created_at=now()))

    async def put_credential(self, user_key: str, profile: str, service: str, ciphertext: bytes) -> None:
        async with self.engine.begin() as c:
            await c.execute(
                delete(credentials).where(
                    (credentials.c.user_key == user_key)
                    & (credentials.c.profile == profile)
                    & (credentials.c.service == service)
                )
            )
            await c.execute(
                insert(credentials).values(
                    user_key=user_key,
                    profile=profile,
                    service=service,
                    ciphertext=ciphertext,
                    updated_at=now(),
                )
            )

    async def get_credential(self, user_key: str, profile: str, service: str) -> bytes | None:
        async with self.engine.connect() as c:
            row = (
                await c.execute(
                    select(credentials.c.ciphertext).where(
                        (credentials.c.user_key == user_key)
                        & (credentials.c.profile == profile)
                        & (credentials.c.service == service)
                    )
                )
            ).first()
        return bytes(row[0]) if row else None

    async def list_credentials(self, user_key: str, profile: str | None = None) -> list[dict]:
        q = select(credentials.c.profile, credentials.c.service, credentials.c.updated_at).where(
            credentials.c.user_key == user_key
        )
        if profile:
            q = q.where(credentials.c.profile == profile)
        async with self.engine.connect() as c:
            return [dict(r._mapping) for r in (await c.execute(q)).all()]

    async def delete_credential(self, user_key: str, profile: str, service: str) -> None:
        async with self.engine.begin() as c:
            await c.execute(
                delete(credentials).where(
                    (credentials.c.user_key == user_key)
                    & (credentials.c.profile == profile)
                    & (credentials.c.service == service)
                )
            )
            await c.execute(
                delete(identity_links).where(
                    (identity_links.c.user_key == user_key)
                    & (identity_links.c.profile == profile)
                    & (identity_links.c.service == service)
                )
            )

    # ------------------------------------------------------------------- identity links
    async def set_identity(
        self,
        user_key: str,
        profile: str,
        service: str,
        moodle_userid: int | None,
        verified: bool,
        evidence: str,
    ) -> None:
        async with self.engine.begin() as c:
            await c.execute(
                delete(identity_links).where(
                    (identity_links.c.user_key == user_key)
                    & (identity_links.c.profile == profile)
                    & (identity_links.c.service == service)
                )
            )
            await c.execute(
                insert(identity_links).values(
                    user_key=user_key,
                    profile=profile,
                    service=service,
                    moodle_userid=moodle_userid,
                    verified=verified,
                    evidence=evidence,
                    checked_at=now(),
                )
            )

    async def get_identities(self, user_key: str, profile: str) -> dict[str, dict]:
        async with self.engine.connect() as c:
            rows = (
                await c.execute(
                    select(identity_links).where(
                        (identity_links.c.user_key == user_key) & (identity_links.c.profile == profile)
                    )
                )
            ).all()
        return {
            r.service: {
                "moodle_userid": r.moodle_userid,
                "verified": bool(r.verified),
                "evidence": r.evidence,
                "checked_at": r.checked_at,
            }
            for r in rows
        }

    # ------------------------------------------------------------------- dev tokens
    async def put_dev_token(self, token_hash: str, user_key: str, expires_at: int) -> None:
        async with self.engine.begin() as c:
            await c.execute(
                insert(dev_tokens).values(
                    token_hash=token_hash, user_key=user_key, expires_at=expires_at, revoked=False
                )
            )

    async def lookup_dev_token(self, token_hash: str) -> dict | None:
        async with self.engine.connect() as c:
            row = (await c.execute(select(dev_tokens).where(dev_tokens.c.token_hash == token_hash))).first()
        if not row or row.revoked or row.expires_at < now():
            return None
        return {"user_key": row.user_key, "expires_at": row.expires_at}

    async def revoke_dev_tokens(self, user_key: str) -> None:
        async with self.engine.begin() as c:
            await c.execute(update(dev_tokens).where(dev_tokens.c.user_key == user_key).values(revoked=True))

    # ------------------------------------------------------------------- snapshots
    async def put_snapshot(self, user_key: str, profile: str, skey: str, payload: Any) -> None:
        async with self.engine.begin() as c:
            await c.execute(
                delete(snapshots).where(
                    (snapshots.c.user_key == user_key)
                    & (snapshots.c.profile == profile)
                    & (snapshots.c.skey == skey)
                )
            )
            await c.execute(
                insert(snapshots).values(
                    user_key=user_key, profile=profile, skey=skey, payload=json.dumps(payload), taken_at=now()
                )
            )

    async def get_snapshot(self, user_key: str, profile: str, skey: str) -> tuple[Any, int] | None:
        async with self.engine.connect() as c:
            row = (
                await c.execute(
                    select(snapshots.c.payload, snapshots.c.taken_at).where(
                        (snapshots.c.user_key == user_key)
                        & (snapshots.c.profile == profile)
                        & (snapshots.c.skey == skey)
                    )
                )
            ).first()
        return (json.loads(row[0]), row[1]) if row else None

    # ------------------------------------------------------------------- write journal
    async def begin_operation(
        self, user_key: str, profile: str, kind: str, target: str, payload_hash: str, op_id: str | None = None
    ) -> tuple[str, dict | None]:
        """Register an intent. If an op with the same id (or same kind+target+hash that completed/ambiguous)
        exists, return it so callers can reconcile instead of re-sending."""
        async with self.engine.begin() as c:
            if op_id:
                row = (
                    await c.execute(
                        select(operations).where(
                            (operations.c.op_id == op_id) & (operations.c.user_key == user_key)
                        )
                    )
                ).first()
                if row:
                    return op_id, _op(row)
            row = (
                await c.execute(
                    select(operations).where(
                        (operations.c.user_key == user_key)
                        & (operations.c.profile == profile)
                        & (operations.c.kind == kind)
                        & (operations.c.target == target)
                        & (operations.c.payload_hash == payload_hash)
                        & (operations.c.status.in_(["done", "ambiguous"]))
                    )
                )
            ).first()
            if row:
                return row.op_id, _op(row)
            new = op_id or uuid.uuid4().hex
            await c.execute(
                insert(operations).values(
                    op_id=new,
                    user_key=user_key,
                    profile=profile,
                    kind=kind,
                    target=target,
                    payload_hash=payload_hash,
                    status="started",
                    evidence="{}",
                    created_at=now(),
                    updated_at=now(),
                )
            )
            return new, None

    async def finish_operation(self, op_id: str, status: str, evidence: dict | None = None) -> None:
        async with self.engine.begin() as c:
            await c.execute(
                update(operations)
                .where(operations.c.op_id == op_id)
                .values(status=status, evidence=json.dumps(evidence or {}), updated_at=now())
            )

    async def list_operations(self, user_key: str, limit: int = 20) -> list[dict]:
        async with self.engine.connect() as c:
            rows = (
                await c.execute(
                    select(operations)
                    .where(operations.c.user_key == user_key)
                    .order_by(operations.c.created_at.desc())
                    .limit(limit)
                )
            ).all()
        return [_op(r) for r in rows]

    # ------------------------------------------------------------------- artifacts (remote uploads)
    async def put_artifact(
        self, user_key: str, filename: str, size: int, sha256: str, path: str, ttl: int
    ) -> str:
        aid = uuid.uuid4().hex
        async with self.engine.begin() as c:
            await c.execute(
                insert(artifacts).values(
                    id=aid,
                    user_key=user_key,
                    filename=filename,
                    size=size,
                    sha256=sha256,
                    path=path,
                    created_at=now(),
                    expires_at=now() + ttl,
                )
            )
        return aid

    async def get_artifact(self, user_key: str, aid: str) -> dict | None:
        async with self.engine.connect() as c:
            row = (
                await c.execute(
                    select(artifacts).where((artifacts.c.id == aid) & (artifacts.c.user_key == user_key))
                )
            ).first()
        if not row or row.expires_at < now():
            return None
        return dict(row._mapping)

    async def expired_artifacts(self) -> list[dict]:
        async with self.engine.connect() as c:
            rows = (await c.execute(select(artifacts).where(artifacts.c.expires_at < now()))).all()
        return [dict(r._mapping) for r in rows]

    async def delete_artifacts(self, ids: list[str]) -> None:
        if ids:
            async with self.engine.begin() as c:
                await c.execute(delete(artifacts).where(artifacts.c.id.in_(ids)))

    async def user_artifacts(self, user_key: str) -> list[dict]:
        async with self.engine.connect() as c:
            rows = (await c.execute(select(artifacts).where(artifacts.c.user_key == user_key))).all()
        return [dict(r._mapping) for r in rows]

    # ------------------------------------------------------------------- manual attendance
    async def put_manual_attendance(self, user_key: str, profile: str, course_id: int, payload: dict) -> None:
        async with self.engine.begin() as c:
            await c.execute(
                delete(manual_attendance).where(
                    (manual_attendance.c.user_key == user_key)
                    & (manual_attendance.c.profile == profile)
                    & (manual_attendance.c.course_id == course_id)
                )
            )
            await c.execute(
                insert(manual_attendance).values(
                    user_key=user_key,
                    profile=profile,
                    course_id=course_id,
                    payload=json.dumps(payload),
                    updated_at=now(),
                )
            )

    async def get_manual_attendance(self, user_key: str, profile: str, course_id: int) -> dict | None:
        async with self.engine.connect() as c:
            row = (
                await c.execute(
                    select(manual_attendance.c.payload).where(
                        (manual_attendance.c.user_key == user_key)
                        & (manual_attendance.c.profile == profile)
                        & (manual_attendance.c.course_id == course_id)
                    )
                )
            ).first()
        return json.loads(row[0]) if row else None

    # ------------------------------------------------------------------- material index
    async def replace_chunks(
        self,
        user_key: str,
        profile: str,
        course_id: int,
        ref: str,
        chunks: list[tuple[str, str]],
        source_url: str,
    ) -> None:
        """chunks: [(location, text)] for one file `ref`."""
        async with self.engine.begin() as c:
            await c.execute(
                delete(material_chunks).where(
                    (material_chunks.c.user_key == user_key)
                    & (material_chunks.c.profile == profile)
                    & (material_chunks.c.ref == ref)
                )
            )
            for loc, text in chunks:
                await c.execute(
                    insert(material_chunks).values(
                        user_key=user_key,
                        profile=profile,
                        course_id=course_id,
                        ref=ref,
                        location=loc,
                        text=text,
                        source_url=source_url,
                        indexed_at=now(),
                    )
                )

    async def get_chunks(
        self, user_key: str, profile: str, course_id: int | None = None, ref: str | None = None
    ) -> list[dict]:
        q = select(material_chunks).where(
            (material_chunks.c.user_key == user_key) & (material_chunks.c.profile == profile)
        )
        if course_id is not None:
            q = q.where(material_chunks.c.course_id == course_id)
        if ref is not None:
            q = q.where(material_chunks.c.ref == ref)
        async with self.engine.connect() as c:
            return [dict(r._mapping) for r in (await c.execute(q.order_by(material_chunks.c.id))).all()]

    # ------------------------------------------------------------------- data lifecycle
    async def purge_user(self, user_key: str, profile: str | None = None) -> dict[str, int]:
        """Delete everything stored for a user (optionally one profile). Returns row counts."""
        counts: dict[str, int] = {}
        async with self.engine.begin() as c:
            for t in (credentials, identity_links, snapshots, manual_attendance, material_chunks):
                cond = t.c.user_key == user_key
                if profile and "profile" in t.c:
                    cond = cond & (t.c.profile == profile)
                counts[t.name] = (await c.execute(delete(t).where(cond))).rowcount
            if not profile:
                for t in (operations, artifacts, dev_tokens, users):
                    counts[t.name] = (await c.execute(delete(t).where(t.c.user_key == user_key))).rowcount
        return counts

    async def export_user(self, user_key: str) -> dict:
        """Everything except secrets (ciphertext is omitted) for data portability."""
        out: dict[str, list] = {}
        async with self.engine.connect() as c:
            for t in (identity_links, snapshots, manual_attendance, operations, artifacts):
                rows = (await c.execute(select(t).where(t.c.user_key == user_key))).all()
                out[t.name] = [dict(r._mapping) for r in rows]
            rows = (
                await c.execute(
                    select(credentials.c.profile, credentials.c.service, credentials.c.updated_at).where(
                        credentials.c.user_key == user_key
                    )
                )
            ).all()
            out["credentials"] = [dict(r._mapping) for r in rows]  # metadata only
            rows = (
                await c.execute(
                    select(
                        material_chunks.c.ref, material_chunks.c.location, material_chunks.c.indexed_at
                    ).where(material_chunks.c.user_key == user_key)
                )
            ).all()
            out["material_chunks"] = [dict(r._mapping) for r in rows]
        return out


def _op(row: Any) -> dict:
    return {
        "op_id": row.op_id,
        "kind": row.kind,
        "target": row.target,
        "status": row.status,
        "evidence": json.loads(row.evidence or "{}"),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
