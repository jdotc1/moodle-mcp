# Architecture

```
MCP client ──stdio──┐                       ┌── Moodle REST (/webservice/rest/server.php, POST form)
                    ├─ FastMCP tools ──┐    ├── /webservice/upload.php (draft area)
MCP client ─HTTP──┐ │  (registry)      ├─ domain/* ── moodle.client ── /webservice/pluginfile.php (token, host-locked)
GPT Actions ─REST─┴─┴─ FastAPI facade ─┘    │
                                    store (SQLite | PostgreSQL)   calc/* pure functions   materials/extract
```

* **`server/registry.py`** — one `@op` per operation. `mcp_app.py` turns each into an MCP tool (annotations from the op), `http_app.py` into `POST /api/v1/tools/<name>` with a pydantic body. Behaviour and descriptions are never duplicated.
* **`server/resolver.py`** — maps the *authenticated* caller to an `Account` (tokens + profile). Local mode: fixed `local` user, tokens from keyring/file. Hosted: verified bearer → `user_key`, tokens decrypted from the DB (`vault.py`). No singleton holds "the current user's token".
* **`moodle/`** — `client.py` (REST/uploads/downloads, error mapping, reads retried with backoff, writes never), `params.py` (PHP-style flattening), `netsafe.py` (destination policy), `catalog.py` (function catalog, provenance, read/view/write classification, parameter validation).
* **`domain/`** — academic logic per area; returns plain dicts with ISO timestamps in the profile timezone.
* **`calc/`** — pure exact arithmetic (`Fraction`/`Decimal`), no I/O: attendance and grades (restricted expression parser, no `eval`).
* **`store.py`** — SQLAlchemy Core; every query is scoped by `(user_key, profile)`. Tables: users, credentials (ciphertext), identity_links, dev_tokens, snapshots, operations (write journal), artifacts, manual_attendance, material_chunks.
* **Write safety** — operations are journaled by `(kind, target, payload hash)`. A lost response is reconciled by reading state; identical content is not re-sent.
* **Hosted MCP** runs `stateless_http` + `json_response`: each request carries its own bearer, verified by `DevTokenVerifier` or `OIDCVerifier`.

Adding a feature: write the domain function, register an `@op`, add its Moodle functions to `permissions.py`, test against the fake, regenerate docs.
