"""Resolve files the model wants to upload: local paths (stdio) or authenticated artifact ids (remote)."""

from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from ..errors import NotFoundError, UnsafeDestinationError, ValidationError
from .account import Account

_FORBIDDEN_PARTS = {
    ".ssh",
    ".gnupg",
    ".aws",
    ".kube",
    ".docker",
    ".config",
    ".git",
    ".netrc",
    ".npmrc",
    ".pypirc",
}
_SAFE_NAME = re.compile(r"[^\w.\- ()\[\]]+", re.UNICODE)


@dataclass
class ResolvedFile:
    name: str
    data: bytes
    sha256: str
    origin: str  # "path" | "artifact"

    @property
    def size(self) -> int:
        return len(self.data)


def safe_filename(name: str) -> str:
    name = os.path.basename(name.replace("\\", "/")).strip().strip(".")
    name = _SAFE_NAME.sub("_", name)[:200]
    if not name:
        raise ValidationError("Invalid file name.")
    return name


def _check_local_path(acct: Account, raw: str) -> Path:
    p = Path(raw).expanduser()
    try:
        real = p.resolve(strict=True)  # resolves symlinks: a link cannot point outside the roots
    except (FileNotFoundError, RuntimeError):
        raise NotFoundError(f"File not found: {raw}") from None
    if not real.is_file():
        raise ValidationError(f"Not a regular file: {raw}")
    if not any(real == r or r in real.parents for r in acct.runtime.upload_roots):
        raise UnsafeDestinationError(
            "That path is outside the folders this server may upload from.",
            hint="Allowed roots: "
            + ", ".join(str(r) for r in acct.runtime.upload_roots)
            + ". Set MAMCP_UPLOAD_ROOTS to change them.",
        )
    if any(part in _FORBIDDEN_PARTS for part in real.parts):
        raise UnsafeDestinationError("Refusing to read from a credentials/config directory.")
    return real


async def resolve_files(acct: Account, refs: list[str]) -> list[ResolvedFile]:
    out: list[ResolvedFile] = []
    for ref in refs:
        if ref.startswith("artifact:"):
            row = await acct.store.get_artifact(acct.user_key, ref.split(":", 1)[1])
            if not row:
                raise NotFoundError(
                    f"Artifact {ref} not found or expired (artifacts are private and temporary)."
                )
            path = Path(row["path"])
            base = acct.runtime.artifact_dir.resolve()
            if base not in path.resolve().parents:
                raise UnsafeDestinationError("Artifact path escapes the artifact directory.")
            data = path.read_bytes()
            out.append(ResolvedFile(row["filename"], data, hashlib.sha256(data).hexdigest(), "artifact"))
        else:
            if acct.runtime.remote:
                raise ValidationError(
                    "Local file paths are not available in remote mode: the server cannot see your disk.",
                    hint="Upload the file first (POST /api/v1/artifacts) and pass 'artifact:<id>'.",
                )
            real = _check_local_path(acct, ref)
            if real.stat().st_size > acct.runtime.max_upload_bytes:
                raise ValidationError("File exceeds the configured upload size limit.")
            data = real.read_bytes()
            out.append(ResolvedFile(safe_filename(real.name), data, hashlib.sha256(data).hexdigest(), "path"))
    names = [f.name for f in out]
    if len(set(names)) != len(names):
        raise ValidationError("Two files have the same name; rename one.")
    return out


async def save_artifact(acct: Account, filename: str, data: bytes) -> dict:
    """Store an uploaded file (remote mode). Enforces size/count/quota limits; name is sanitised."""
    rt = acct.runtime
    if len(data) > rt.max_upload_bytes:
        raise ValidationError(f"File too large (limit {rt.max_upload_bytes} bytes).")
    existing = await acct.store.user_artifacts(acct.user_key)
    live = [a for a in existing if a["expires_at"] >= time.time()]
    if (
        len(live) >= rt.max_artifacts_per_user
        or sum(a["size"] for a in live) + len(data) > rt.max_artifact_bytes_per_user
    ):
        raise ValidationError("Artifact quota reached; wait for expiry or delete old artifacts.")
    name = safe_filename(filename)
    sha = hashlib.sha256(data).hexdigest()
    udir = rt.artifact_dir / hashlib.sha256(acct.user_key.encode()).hexdigest()[:24]
    udir.mkdir(parents=True, exist_ok=True)
    path = udir / f"{sha[:16]}-{name}"
    path.write_bytes(data)
    aid = await acct.store.put_artifact(
        acct.user_key, name, len(data), sha, str(path), rt.artifact_ttl_seconds
    )
    return {
        "artifact": f"artifact:{aid}",
        "filename": name,
        "size": len(data),
        "sha256": sha,
        "expires_in_seconds": rt.artifact_ttl_seconds,
    }


async def cleanup_artifacts(
    store,
) -> int:
    rows = await store.expired_artifacts()
    for r in rows:
        try:
            Path(r["path"]).unlink(missing_ok=True)
        except OSError:
            pass
    await store.delete_artifacts([r["id"] for r in rows])
    return len(rows)
