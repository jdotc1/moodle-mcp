"""Packaging guards: what `uvx --from git+...` installs by default must be enough for core features."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _core_dependency_names() -> set[str]:
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    return {d.split("[")[0].split(">")[0].split("<")[0].split("=")[0].strip().lower() for d in deps}


def test_pdf_extraction_works_without_any_extra():
    # The plugin starts the server without extras; PDFs are the main material format.
    assert "pypdf" in _core_dependency_names()


def test_pdf_extra_is_kept_for_backwards_compatibility():
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"]
    assert "pdf" in extras
