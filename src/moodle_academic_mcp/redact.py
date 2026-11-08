"""Credential redaction for logs, errors, tool results and diagnostics."""

from __future__ import annotations

import logging
import re
from typing import Any

REDACTED = "[REDACTED]"

# Field names whose values must never leave the process.
SENSITIVE_KEYS = {
    "wstoken",
    "token",
    "privatetoken",
    "userprivateaccesskey",
    "password",
    "passwd",
    "authorization",
    "access_token",
    "refresh_token",
    "client_secret",
    "secret",
    "api_key",
    "apikey",
    "key",
    "sesskey",
    "accesskey",
    "cookie",
    "set-cookie",
}

_URL_TOKEN = re.compile(
    r"(?i)([?&;/](?:wstoken|token|privatetoken|key|sesskey|access_token)[=/])([^&\s\"\'#/]+)"
)
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
# Moodle serves private files as /tokenpluginfile.php/<token>/rest-of-path
_TOKENPLUGINFILE = re.compile(r"(?i)(/tokenpluginfile\.php/)([^/\s\"\'#]+)")
_KV = re.compile(
    r"(?i)\b(wstoken|token|password|userprivateaccesskey|privatetoken)(\s*[=:]\s*)([^\s,&\"\']+)"
)
# Set-Cookie / Cookie headers: redact sensitive cookie names (and Moodle session cookies).
_COOKIE_PAIR = re.compile(
    r"(?i)(\b(?:set-)?cookie\b[^\r\n]*?(?:^|[;\s])(?:wstoken|token|privatetoken|userprivateaccesskey|"
    r"sesskey|MoodleSession[^=;\s]*)(=))([^;\s\r\n]+)"
)
_registered: set[str] = set()


def register_secret(value: str | None) -> None:
    """Remember a literal secret so it is scrubbed wherever it shows up."""
    if value and len(value) >= 8:
        _registered.add(value)


def forget_secret(value: str | None) -> None:
    _registered.discard(value or "")


def scrub_text(text: str) -> str:
    out = _URL_TOKEN.sub(lambda m: m.group(1) + REDACTED, text)
    out = _BEARER.sub(lambda m: m.group(1) + REDACTED, out)
    out = _KV.sub(lambda m: m.group(1) + m.group(2) + REDACTED, out)
    out = _TOKENPLUGINFILE.sub(lambda m: m.group(1) + REDACTED, out)
    out = _COOKIE_PAIR.sub(lambda m: m.group(1) + REDACTED, out)  # group(1) already includes '='
    for s in sorted(_registered, key=len, reverse=True):
        out = out.replace(s, REDACTED)
    return out


def scrub(obj: Any) -> Any:
    """Recursively redact dict keys that are sensitive and scrub strings."""
    if isinstance(obj, dict):
        return {
            k: (REDACTED if isinstance(k, str) and k.lower() in SENSITIVE_KEYS and v else scrub(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(scrub(v) for v in obj)
    if isinstance(obj, str):
        return scrub_text(obj)
    return obj


def strip_url_credentials(url: str) -> str:
    """Return the URL without token/key query params or userinfo (safe to show to a model)."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in SENSITIVE_KEYS
    ]
    path = parts.path
    if "/tokenpluginfile.php/" in path:
        # tokenpluginfile.php/<token>/rest-of-path -> pluginfile.php/rest-of-path
        head, _, tail = path.partition("/tokenpluginfile.php/")
        _, _, rest = tail.partition("/")
        path = f"{head}/pluginfile.php/{rest}"
    return urlunsplit((parts.scheme, host, path, urlencode(query), parts.fragment))


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = scrub_text(record.getMessage())
            record.args = ()
        except Exception:  # pragma: no cover
            pass
        return True


def setup_logging(level: str = "INFO") -> None:
    """Log to stderr only (stdout belongs to the MCP protocol in stdio mode)."""
    import sys

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root.addHandler(handler)
    root.setLevel(level.upper())
    # httpx logs full URLs (incl. query) at INFO: keep it quiet.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("mcp").setLevel(logging.WARNING)
    logging.getLogger("pypdf").setLevel(logging.ERROR)  # noisy on slightly malformed PDFs
