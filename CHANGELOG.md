# Changelog

## Unreleased

## 0.1.3
- **PDF reading now works out of the box.** `pypdf` is a core dependency; before, the plugin (which starts the server via `uvx` without extras) could not read any PDF: course slides and assignment statements failed with `ImportError`. The `pdf` extra remains as an empty alias.

## 0.1.2
- `list_deadlines` items carry `window_match` (due | cutoff | past_due) to explain why they are listed.
- Quiz writes (`start`, `save`, `finish`) are recorded in the operations journal (`list_operations`) with page, fields, `finish_requested` and the resulting attempt state, so an attempt that closes unexpectedly can be traced.
- Saving to a quiz attempt that is no longer in progress now fails with "answers were NOT saved" instead of a bare Moodle error.
- Moodle's `invalidresponse` (seen on `gradereport_user_get_grade_items`) is reported as a server-side error with a workaround; `dmlwriteexception` gets an actionable message too.
- Group assignments are detected from the real Moodle shape (`submissiongroup`/`usergroups`), not the non-existent `teamsubmissionenabled`.

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
