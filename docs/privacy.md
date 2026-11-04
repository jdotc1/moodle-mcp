# Data, retention and deletion

What the integration stores:
| Data | Where | Retention |
| --- | --- | --- |
| Moodle tokens | local: keyring / 0600 file; hosted: encrypted DB column | until you delete them |
| Material text index (chunks of files you read/indexed) | SQLite/Postgres | until purge |
| Snapshots for `changes_since` (assignment dates) | DB | until purge |
| Write journal (kind, assignment id, content hash, status, evidence) | DB | until purge |
| Manual attendance records | DB | until purge |
| Uploaded artifacts (hosted) | `MAMCP_ARTIFACT_DIR` + DB row | 24 h default, cleaned periodically; per-user quota |

Commands: `moodle-academic-mcp data export` (no secrets) and `data purge --yes [--profile P]`; hosted users: `GET /api/v1/me/export`, `DELETE /api/v1/me/data` (credentials, index, journal, artifacts, dev tokens).

What leaves your machine: whatever the tools return is processed by **your AI provider** — also in local mode. The server itself sends data only to the configured Moodle host. Course material may be copyrighted/private to the course: do not index or share it beyond your own study use. Logs go to stderr and are redacted; no telemetry.
