# Security policy

Report vulnerabilities **privately** via GitHub Security Advisories on this repository (not public issues). Include a minimal reproduction with synthetic data; never include real tokens.

## Model
- Moodle tokens are bearer credentials of the student. They are stored in the OS keyring / a 0600 file (local) or encrypted with a key held outside the database (hosted, Fernet with owner binding, key rotation supported).
- Tokens are sent only in POST bodies to the profile's Moodle host. Redirects on downloads are re-validated; the hosted mode resolves DNS and blocks loopback/private/link-local/metadata addresses unless an administrator opts in per profile.
- Logs, errors, tool results and diagnostics pass through redaction; `userprivateaccesskey` is dropped from site info.
- The MCP bearer (OIDC/dev token) authenticates a user to this server only and is never a Moodle token. Identity comes from the verified token, never from model-supplied ids. Hosted MCP runs stateless HTTP (no session ids to hijack).
- Course material, forum posts and VPL output are untrusted data; they are labelled and cannot trigger actions by themselves, but a model may still be influenced by them — keep your client's confirmation policy on for writes if you are unsure.
- Uploaded files are never executed; local uploads are confined to allowed roots (symlinks resolved, config/credential dirs refused).

## Known limits
Residual DNS-rebinding window between resolution and connect; no rate limiting beyond onboarding endpoints; OIDC onboarding and ChatGPT/Claude OAuth flows are documented but not validated against a real IdP.
