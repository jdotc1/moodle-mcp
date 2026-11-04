# Contributing

1. `uv sync --all-extras`; run `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src`.
2. Tests use the **mock** Moodle in `tests/fake_moodle.py`. It checks our protocol handling but is not evidence about a real Moodle. Never mark a feature `validated_sandbox`/`validated_ufsc` (in `src/moodle_academic_mcp/data/validation.json`) unless you ran it against that real system and describe the date/version.
3. **Never commit real data**: tokens, names, grades, course files, screenshots, wsdoc PDF exports, `.env`. Use synthetic fixtures. PDFs from your account stay out of git (`.gitignore`).
4. New Moodle call? Add it to the catalog with provenance (`moodle/catalog.py` or regenerate with `scripts/build_catalog.py`), classify read/write correctly, add it to `server/permissions.py`, document effects, and add tests. Regenerate `docs/tools.md` with `uv run python scripts/gen_tools_doc.py`.
5. Do not add blockers based on whether a request "looks like homework", and do not add mandatory extra confirmations: policy belongs to the client/user. Do add accurate descriptions and annotations.
6. Do not bypass network restrictions (VPN/SSO), escalate privileges, or call admin/teacher functions on students' behalf.
7. Small PRs, conventional commit messages. Good first issues: [docs/good-first-issues.md](docs/good-first-issues.md).
