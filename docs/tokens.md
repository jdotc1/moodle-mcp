# Tokens, network and credential protection

## Getting a token
Open `<moodle>/user/managetoken.php` (UFSC: `https://presencial.moodle.ufsc.br/user/managetoken.php`), pick the service and copy its key. Observed on one UFSC student account: services **Moodle mobile web service**, **Attendance** and **VPL web service** — other accounts may differ.

| Service name in `configure` | Used for |
| --- | --- |
| `mobile` | everything except attendance/VPL (this is the primary identity) |
| `attendance` | `mod_attendance_*` |
| `vpl` | `mod_vpl_*` |

Each token is a separate credential for a separate service. The server does **not** assume tokens belong to the same person: it compares `core_webservice_get_site_info().userid` when a service exposes that function; otherwise the identity stays `unknown` and records are not mixed (attendance then requires manual import).

## Storing
`moodle-academic-mcp configure` reads tokens with hidden input and stores them in the OS keyring when available, else `~/.config/moodle-academic-mcp/secrets.json` (mode 0600). Env override: `MOODLE_TOKEN_<PROFILE>_<SERVICE>`. Rotate by running `configure` again; revoke at `managetoken.php` and `moodle-academic-mcp data purge --yes`.

Never paste a token in an issue, chat history or screenshot. If you did, reset it at `managetoken.php`.

## Network / VPN
UFSC announced temporary restrictions on external access and VPN requirements for changes (communication of October 2026 — verify the current status, it is not a permanent rule). Symptoms: HTML instead of JSON, redirects to a login/SSO page, timeouts. `moodle-academic-mcp doctor` reports stage `network`. **This project never tries to bypass network restrictions**; connect through the institution's approved VPN if required.

## Self-hosted/private Moodle
URLs must be `https://`. Private-network targets are refused in hosted mode unless the administrator sets `allow_private_networks = true` for that profile.

## Attendance token that cannot prove its identity ("permission_denied" on site info)
Many Attendance services do not expose `core_webservice_get_site_info`, so the server cannot check that the token belongs to the same user as your `mobile` token. It then refuses to read records (it will not mix identities). If the token is yours (you created it on **your own** `managetoken.php` page), declare it once:

```bash
moodle-academic-mcp configure --profile ufsc --service attendance        # store the token (hidden input)
moodle-academic-mcp configure --profile ufsc --attest attendance         # "this token is mine"
moodle-academic-mcp configure --profile ufsc --attendance-min-percent 75% \
    --attendance-rule-source "plano de ensino de cada disciplina, 2026/2"   # optional defaults
```
Reports then carry `identity: attested_by_user` (not "verified"). A proven *mismatch* is never overridden. The minimum frequency is **your** input with a source (the server never assumes 75 %). Without any token: `attendance_import_manual`.

If your terminal has no keyring, add `MAMCP_NO_KEYRING=1` to the command; tokens are then read from the 0600 file, and the server finds them there too.
