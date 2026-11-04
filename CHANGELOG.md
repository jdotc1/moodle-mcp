# Changelog

## Unreleased
- `list_deadlines` items carry `window_match` (due | cutoff | past_due) to explain why they are listed.

## 0.1.1
- Claude Code plugin + marketplace (`/plugin marketplace add matheussilvano/moodle-mcp`), guided skills, token via secure `userConfig`.
- Profile from environment variables (`MAMCP_BASE_URL` …) now becomes the default profile; the plugin isolates its config/state under `CLAUDE_PLUGIN_DATA`.
- Latest-term course filter, bounded past-due, user-attested identity for services that hide their user id.
- `.odt/.ods/.odp` extraction (community PR).

## 0.1.0 — unreleased
- Moodle REST client (nested params, error mapping, bounded read retries, no write retries), draft-area upload, safe downloads.
- 55 MCP tools, 7 prompts; stdio and stateless Streamable HTTP; FastAPI facade/OpenAPI for GPT Actions; onboarding page.
- Assignments, deadlines, quizzes, forums, grades, groups (Group choice), materials, attendance math, VPL adapter, generic invoker.
- SQLite/PostgreSQL storage, encrypted per-user credentials, artifacts, data export/purge.
- Bundled function catalog generated from a UFSC `wsdoc` export (API shapes only).
