# Distribution: where this can be published and what is still manual

| Channel | State | What it takes |
| --- | --- | --- |
| **Claude Code plugin marketplace** (this repo) | **Live** — `/plugin marketplace add matheussilvano/moodle-mcp` | Nothing more. `claude plugin validate --strict ./plugin` passes. Pinned to git tag `v0.1.3`. |
| **Anthropic directory** (claude.ai / Cowork / Claude Code sync) | Not submitted | Paid claude.ai plan, submit at <https://claude.ai/directory/manage>; run the portal's pre-submission checklist. `plugin.json` already carries support/privacy/documentation URLs; an `icon` image is still needed. Review is Anthropic's; a Python/`uvx` server may need justification. |
| **PyPI** (`uvx moodle-academic-mcp`) | **Not published** | Create the PyPI project, configure a *trusted publisher* (repo `matheussilvano/moodle-mcp`, workflow `publish-pypi.yml`, environment `pypi`), set repo variable `PYPI_ENABLED=true`, publish a GitHub release. Then drop the `git+https://…@v0.1.3` form from the docs. |
| **Official MCP Registry** | Not published | Needs the PyPI package first. `server.json` and the `mcp-name` marker in `README.md` (the PyPI description) are ready. Then `mcp-publisher login github && mcp-publisher publish`. |
| **Claude Desktop extension (.mcpb)** | Not built | Possible (MCPB supports Python via `uv`); not attempted or tested. Desktop users can use the generic JSON config in `examples/mcp.json`. |
| **ChatGPT / Actions** | Needs hosted deployment | See `auth.md`; nothing to publish centrally — each institution/community hosts its own instance. |

## Release checklist
1. `make check`; update `CHANGELOG.md`; bump `version` in `pyproject.toml`, `plugin/.claude-plugin/plugin.json`, `server.json`, and the `@vX.Y.Z` pin in `plugin/.mcp.json`.
2. `git tag vX.Y.Z && git push --tags` (the plugin installs the server from that tag, so the tag must exist before users update).
3. GitHub release → artifacts attach automatically; PyPI publishes if enabled.

## Why the plugin pins a tag
`uvx --from git+…@v0.1.3` makes installs reproducible and means a new push to `main` cannot silently change what runs with your Moodle token. Updating is an explicit version bump.
