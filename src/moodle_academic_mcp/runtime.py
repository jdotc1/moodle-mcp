"""Runtime knobs shared by all tools (local vs hosted behaviour)."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Runtime:
    mode: str = "local"  # "local" (stdio, single user) | "remote" (hosted, multi-user)
    upload_roots: list[Path] = field(default_factory=lambda: default_upload_roots())
    artifact_dir: Path = field(default_factory=lambda: Path(tempfile.gettempdir()) / "mamcp-artifacts")
    max_upload_bytes: int = 50 * 1024 * 1024
    artifact_ttl_seconds: int = 24 * 3600
    max_artifacts_per_user: int = 50
    max_artifact_bytes_per_user: int = 500 * 1024 * 1024
    # Materials extraction cap, to keep the model context and the process memory small.
    max_extract_chars: int = 2_000_000

    @property
    def remote(self) -> bool:
        return self.mode == "remote"


def default_upload_roots() -> list[Path]:
    env = os.environ.get("MAMCP_UPLOAD_ROOTS")
    if env:
        return [Path(p).expanduser().resolve() for p in env.split(os.pathsep) if p]
    return [Path.cwd().resolve(), Path(tempfile.gettempdir()).resolve()]
