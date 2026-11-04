# Hosted mode and authentication

Hosted mode serves several users from one process: `moodle-academic-mcp serve --transport http` (Docker/Compose provided). Required: `MAMCP_PROFILES_FILE` (institutions; users cannot add URLs), `MAMCP_DATABASE_URL` (PostgreSQL recommended), `MAMCP_ENCRYPTION_KEY` (Fernet key kept **outside** the DB; comma-separate to rotate), `MAMCP_PUBLIC_URL`.

Two separate credentials:
1. **Server bearer** (who you are to *this* server) — OAuth/OIDC access token (production) or a dev token.
2. **Moodle tokens** (what this server uses at Moodle) — added at `/onboarding`, tested immediately, stored encrypted, never returned.

Logging in to this server is **not** an institutional (UFSC) login and there is no "Sign in with UFSC": association is by the student's personal Moodle token. Never ask for institutional passwords.

## Development (reproducible)
```bash
export MAMCP_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
docker compose up --build            # app on 127.0.0.1:8080 + PostgreSQL, dev auth
docker compose exec app moodle-academic-mcp dev-token alice   # prints a bearer (valid 7 days)
```
Open `http://localhost:8080/onboarding`, paste the dev bearer, add your Moodle token. MCP endpoint: `http://localhost:8080/mcp` with `Authorization: Bearer <dev token>`.

## Production (OIDC)
Use an existing provider (Keycloak, Auth0, Entra ID, …) — do not write an authorization server. Set `MAMCP_AUTH_MODE=oidc`, `MAMCP_OIDC_ISSUER`, `MAMCP_OIDC_AUDIENCE` (default `<public url>/mcp`). The server validates JWT signature (JWKS), `iss`, `aud`, `exp`, `sub` and publishes RFC 9728 protected-resource metadata at `/.well-known/oauth-protected-resource/mcp` through the MCP SDK. Clients such as ChatGPT/Claude need the IdP to support their registration method (DCR or CIMD) and PKCE. User key = `iss|sub`.

Onboarding page: set `MAMCP_ONBOARDING_CLIENT_ID`, `MAMCP_OIDC_AUTHORIZE_URL`, `MAMCP_OIDC_TOKEN_URL` for an authorization-code + PKCE login, or paste a bearer.

**Status:** dev auth is tested end-to-end (real MCP client over HTTP). OIDC verification is implemented and unit-wired but **not validated against a real IdP**; the PKCE onboarding flow is untested.

## Reverse proxy / hardening
Terminate TLS in front; set `MAMCP_PUBLIC_URL` (enables DNS-rebinding host/origin allow-list). Private Moodle hosts are refused unless `allow_private_networks = true` in that profile. Artifacts live in `MAMCP_ARTIFACT_DIR` (default TTL 24 h, quotas per user).

## GPT Actions
`GET /openapi-actions.json` is a curated subset (≤30 operations) of the same operations. In the GPT builder: Import from URL, authentication = OAuth with your IdP's authorize/token URLs. Actions are not MCP; they call `POST /api/v1/tools/<op>`.
