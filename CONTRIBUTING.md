# Contributing

Thanks for helping! You do **not** need a Moodle account to contribute: the test suite runs against a mock Moodle, offline.

## 5-minute start
```bash
git clone https://github.com/matheussilvano/moodle-mcp && cd moodle-mcp
make setup      # uv sync + pre-commit hooks
make check      # ruff + mypy + pytest (what CI runs)
```
Pick an issue labelled [`good first issue`](https://github.com/matheussilvano/moodle-mcp/labels/good%20first%20issue), comment that you're taking it, open a draft PR early.

## Where things live
| I want to… | Touch |
| --- | --- |
| add/change a tool | `domain/<area>.py` → `server/ops.py` (`@op`) → `server/permissions.py` → `make docs` |
| support a Moodle function | `moodle/catalog.py` (or `scripts/build_catalog.py`) |
| fix math (attendance/grades) | `calc/` + `tests/test_calc.py` (pure, easiest place to start) |
| extend the mock Moodle | `tests/fake_moodle.py` |
| add a university | [docs/add-institution.md](docs/add-institution.md) |

## Example: add a tiny read tool
1. Write `async def my_thing(acct, course_id) -> dict` in a `domain/` module using `acct.client().call("<moodle_function>", {...})`.
2. Register in `server/ops.py` with `@op(tags=[...])` and a docstring: first line = when to use + effects, then an `Args:` block (becomes the JSON-schema descriptions for MCP *and* the REST/OpenAPI facade).
3. List the Moodle functions in `server/permissions.py` (a test checks they exist in the catalog and that read-only tools call only read functions).
4. Add the function to `tests/fake_moodle.py` and a test; run `make docs && make check`.


1. `uv sync --all-extras`; run `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src`.
2. Tests use the **mock** Moodle in `tests/fake_moodle.py`. It checks our protocol handling but is not evidence about a real Moodle. Never mark a feature `validated_sandbox`/`validated_ufsc` (in `src/moodle_academic_mcp/data/validation.json`) unless you ran it against that real system and describe the date/version.
3. **Never commit real data**: tokens, names, grades, course files, screenshots, wsdoc PDF exports, `.env`. Use synthetic fixtures. PDFs from your account stay out of git (`.gitignore`).
4. New Moodle call? Add it to the catalog with provenance (`moodle/catalog.py` or regenerate with `scripts/build_catalog.py`), classify read/write correctly, add it to `server/permissions.py`, document effects, and add tests. Regenerate `docs/tools.md` with `uv run python scripts/gen_tools_doc.py`.
5. Do not add blockers based on whether a request "looks like homework", and do not add mandatory extra confirmations: policy belongs to the client/user. Do add accurate descriptions and annotations.
6. Do not bypass network restrictions (VPN/SSO), escalate privileges, or call admin/teacher functions on students' behalf.
7. Small PRs, conventional commit messages. Good first issues: [docs/good-first-issues.md](docs/good-first-issues.md).
