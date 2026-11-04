<p align="center"><img src="assets/banner.svg" alt="moodle-academic-mcp" width="100%"></p>

<p align="center"><a href="https://github.com/matheussilvano/moodle-mcp/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/matheussilvano/moodle-mcp/actions/workflows/ci.yml/badge.svg"></a> <img alt="MCP" src="https://img.shields.io/badge/MCP-stdio%20%2B%20Streamable%20HTTP-6f42c1"> <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-22c55e"> <img alt="PRs welcome" src="https://img.shields.io/badge/PRs-welcome-f97316"></p>

# moodle-academic-mcp

A **community, independent** [MCP](https://modelcontextprotocol.io) server that connects a student's own Moodle account to Claude Code, Codex, ChatGPT and other clients. You bring **your AI** and **your tokens**; the server exposes data and actions, your client's model reads, explains, writes and generates files. No paid LLM API is needed by the maintainer.

> Not affiliated with UFSC, Moodle, Anthropic or OpenAI. The first target is UFSC's Moodle "Presencial"; the architecture supports other institutions ([guide](docs/add-institution.md)). Docs are mostly in English under `docs/`; the Portuguese README is [README.md](README.md).

<p align="center"><img src="assets/moodle-logo.png" alt="Moodle logo" width="72"><br><sub>The Moodle® logo is used only to show which platform this project connects to.<br><b>This project is unofficial</b>: it is not made, endorsed or approved by Moodle Pty Ltd.</sub></p>

## Quick start (local, stdio)

Python 3.11+ and [uv](https://docs.astral.sh/uv/). **Not published on PyPI yet** — install from GitHub or a wheel:

```bash
uvx --from git+https://github.com/matheussilvano/moodle-mcp moodle-academic-mcp version
moodle-academic-mcp configure --profile ufsc --service mobile   # hidden input; keyring or 0600 file
moodle-academic-mcp doctor                                       # network, token, Moodle version, available features
claude mcp add --scope user --transport stdio moodle -- uvx --from git+https://github.com/matheussilvano/moodle-mcp moodle-academic-mcp serve
codex mcp add moodle -- uvx --from git+https://github.com/matheussilvano/moodle-mcp moodle-academic-mcp serve
```

ChatGPT and custom GPTs need the [hosted mode](docs/auth.md) (public HTTPS).

## What it does

55 tools: courses, contents, assignments (statement, individual/group status, real upload → draft → submit flow with read-back evidence), cross-module deadlines (assignments + quizzes + calendar, explicit window), quizzes (start/answer/finish using Moodle's own field names), forums/announcements and change tracking, grades with deterministic what-if math, attendance math with weighted meetings, group choice join/leave, materials extraction (PDF/DOCX/PPTX/HTML/text with page references), VPL adapter, and a schema-validated generic invoker. See [docs/tools.md](docs/tools.md).

## Validation status

- Automated: 65+ tests against a **mock** Moodle, real PostgreSQL 16, real MCP clients over stdio and Streamable HTTP.
- Real UFSC Moodle (4.1.5+): **read-only** checks only (site info, courses, assignments/status, calendar, forum list).
- **Writes have not been run against any real Moodle** (mock only). Attendance/VPL/Group-choice adapters follow upstream source and are unvalidated on UFSC. See [feature-matrix](docs/feature-matrix.md) and [sandbox](docs/sandbox.md).

MIT licensed. See [SECURITY](SECURITY.md), [CONTRIBUTING](CONTRIBUTING.md), [docs/privacy.md](docs/privacy.md).

## Contribute

```bash
git clone https://github.com/matheussilvano/moodle-mcp && cd moodle-mcp
make setup && make check     # ~10 s, no credentials, no network
```
See [good first issues](https://github.com/matheussilvano/moodle-mcp/labels/good%20first%20issue), the [roadmap](docs/roadmap.md) and [CONTRIBUTING](CONTRIBUTING.md). The most valuable contribution: validate write flows on a real Moodle sandbox ([docs/sandbox.md](docs/sandbox.md)).
