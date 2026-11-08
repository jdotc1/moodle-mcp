# Clients

Checked against vendor docs on 2026-11-04. "Tested" below means what I actually ran.

| Client | Mode | Config documented | Tested |
| --- | --- | --- | --- |
| Claude Code | stdio | yes | MCP stdio handshake/tools with the official Python MCP client; **not** with the Claude Code app |
| Claude Code | Streamable HTTP + bearer | yes | same client library over HTTP; **not** the app |
| Codex CLI / extension | stdio, HTTP | yes | **not tested** (no access) |
| Claude Desktop | stdio (JSON config) | generic | **not tested** |
| ChatGPT (web) | remote MCP | yes | **not tested**; needs public HTTPS + OAuth |
| Custom GPT Actions | OpenAPI | yes | OpenAPI served and calls tested with bearer; **not** in the GPT builder |

## Claude Code
```bash
claude mcp add --transport stdio moodle -- uvx --from git+https://github.com/matheussilvano/moodle-mcp moodle-academic-mcp serve
claude mcp add --transport http moodle-remote https://mcp.example.org/mcp --header "Authorization: Bearer $MAMCP_TOKEN"
# OAuth server: claude mcp add --transport http moodle-remote https://mcp.example.org/mcp  then /mcp or `claude mcp login moodle-remote`
claude mcp list ; claude mcp get moodle        # status; /mcp inside a session
```
Project file (`.mcp.json`): see `examples/mcp.json`.

## Codex (CLI, IDE, app share `~/.codex/config.toml`)
```bash
codex mcp add moodle -- uvx --from git+https://github.com/matheussilvano/moodle-mcp moodle-academic-mcp serve
codex mcp add moodle-remote --url https://mcp.example.org/mcp && codex mcp login moodle-remote
```
```toml
[mcp_servers.moodle]
command = "uvx"
args = ["--from", "git+https://github.com/matheussilvano/moodle-mcp", "moodle-academic-mcp", "serve"]
default_tools_approval_mode = "writes"      # auto | prompt | writes | approve
# [mcp_servers.moodle-remote]  url = "https://mcp.example.org/mcp"  bearer_token_env_var = "MAMCP_TOKEN"
```

## Claude Desktop
Add to its MCP config JSON the same command as `examples/mcp.json` (`command`/`args`/`env`). Remote servers use the app's connector UI. Config locations vary by version — check the current Claude Desktop docs.

## ChatGPT (web)
OpenAI's docs (checked 2026-11-04): *Plugins → + → Add custom MCP server*, Server URL, authentication (OAuth with static credentials, CIMD or DCR; or none), accept the risk warning, *Create as a plugin*, then install it. It appears in your personal plugins or the workspace where created; availability depends on plan/workspace policy (Lockdown etc.) — not verified here. ChatGPT uses `readOnlyHint`; tools without it are treated as writes and ask for confirmation, which matches how this server annotates tools. Publishing/distributing to others is a separate process not covered here. Needs the [hosted mode](auth.md).

## Custom GPT (Actions)
Not MCP. Use `/openapi-actions.json` ([auth.md](auth.md#gpt-actions)).

## Windows
Install `uv`: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`, then **open a new terminal** (most common error: PATH not refreshed, so `uv`/`uvx` is "not recognized").
PowerShell quoting uses double quotes: `claude mcp add --transport http moodle-remote https://mcp.example.org/mcp --header "Authorization: Bearer $env:MAMCP_TOKEN"` and `codex mcp add moodle -- uvx --from git+https://github.com/matheussilvano/moodle-mcp moodle-academic-mcp serve`.
Config/state live under `%USERPROFILE%\.codex\config.toml`, `%APPDATA%\Claude`, and the project `.mcp.json`.

## Política de confirmação
The server adds **no** mandatory confirmation. Autonomous use: allow the tools in your client (Claude Code `permissions.allow`, Codex `default_tools_approval_mode = "approve"`). Cautious use: leave writes on prompt (`"writes"`/default). `prepare_submission` is an optional dry run. An explicit "send it" request authorises `deliver_assignment`; if intent is ambiguous the prompts ask only for the missing piece. Your client's and institution's policies still apply; the server cannot control how a model responds.

## Compatibility matrix for other clients
Needs: MCP 2025-06+ tools; Streamable HTTP for remote; `structuredContent` helps but is optional (text content is always returned). Clients without prompt support can follow [workflows.md](workflows.md).
