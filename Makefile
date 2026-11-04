.PHONY: setup check test lint fmt types docs build serve doctor
setup:        ## install deps and git hooks
	uv sync --all-extras --group dev
	-uv run pre-commit install
check: lint types test   ## everything CI runs
test:
	uv run pytest -q
lint:
	uv run ruff check src tests scripts
	uv run ruff format --check src tests scripts
fmt:
	uv run ruff format src tests scripts
	uv run ruff check --fix src tests scripts
types:
	uv run mypy src
docs:         ## regenerate docs/tools.md from the operation registry
	uv run python scripts/gen_tools_doc.py
build:
	uv build
serve:        ## run the stdio server (for manual debugging)
	uv run moodle-academic-mcp serve
doctor:
	uv run moodle-academic-mcp doctor
