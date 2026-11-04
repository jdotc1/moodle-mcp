"""Configuration: profiles (institutions), token storage and runtime settings.

Local mode keeps everything on the user's machine:
  ~/.config/moodle-academic-mcp/config.toml    (non-secret: profiles)
  OS keyring, or ~/.config/moodle-academic-mcp/secrets.json (chmod 600) for tokens
  ~/.local/share/moodle-academic-mcp/state.sqlite3  (cache, journals)
"""

from __future__ import annotations

import json
import os
import re
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ConfigError, NotConfiguredError
from .moodle.client import Endpoint
from .redact import register_secret

KEYRING_SERVICE = "moodle-academic-mcp"
SERVICES = ("mobile", "attendance", "vpl")  # conventional names; any name is accepted


def config_dir() -> Path:
    base = os.environ.get("MAMCP_CONFIG_DIR")
    return (
        Path(base)
        if base
        else Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "moodle-academic-mcp"
    )


def data_dir() -> Path:
    base = os.environ.get("MAMCP_DATA_DIR")
    return (
        Path(base)
        if base
        else Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "moodle-academic-mcp"
    )


@dataclass
class ProfileSettings:
    name: str
    base_url: str
    institution: str = ""
    timezone: str = "UTC"
    allow_insecure_http: bool = False
    allow_private_networks: bool = False
    extra_hosts: list[str] = field(default_factory=list)
    attendance_min_percent: str | None = None  # configurable rule, never a universal constant
    attendance_rule_source: str | None = None
    attested_services: list[str] = field(default_factory=list)  # user-attested same-account tokens
    timeout: float = 30.0

    def endpoint(self) -> Endpoint:
        return Endpoint(
            self.base_url,
            self.allow_insecure_http,
            self.allow_private_networks,
            tuple(self.extra_hosts),
            self.timeout,
        )

    def to_public(self) -> dict:
        return {
            "name": self.name,
            "base_url": self.base_url,
            "institution": self.institution or self.name,
            "timezone": self.timezone,
        }


def validate_profile_name(name: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", name):
        raise ConfigError("Profile names use lowercase letters, digits, '-' and '_' (max 32).")
    return name


def normalize_base_url(url: str, *, allow_insecure_http: bool = False) -> str:
    url = url.strip().rstrip("/")
    for suffix in ("/webservice/rest/server.php", "/user/managetoken.php", "/login/index.php"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    if url.startswith("http://") and not allow_insecure_http:
        raise ConfigError(
            "Moodle URLs must use https:// (http:// only with allow_insecure_http for local dev)."
        )
    if not re.match(r"^https?://[^/\s]+", url):
        raise ConfigError("Invalid Moodle base URL.")
    return url


def _profile_from_dict(name: str, d: dict[str, Any]) -> ProfileSettings:
    if "base_url" not in d:
        raise ConfigError(f"Profile {name!r} has no base_url.")
    return ProfileSettings(
        name=name,
        base_url=normalize_base_url(d["base_url"], allow_insecure_http=bool(d.get("allow_insecure_http"))),
        institution=d.get("institution", name),
        timezone=d.get("timezone", "UTC"),
        allow_insecure_http=bool(d.get("allow_insecure_http", False)),
        allow_private_networks=bool(d.get("allow_private_networks", False)),
        extra_hosts=list(d.get("extra_hosts", [])),
        attendance_min_percent=d.get("attendance_min_percent"),
        attendance_rule_source=d.get("attendance_rule_source"),
        attested_services=list(d.get("attested_services", [])),
        timeout=float(d.get("timeout", 30.0)),
    )


def builtin_profile_presets() -> dict[str, dict[str, Any]]:
    """Presets are *suggestions* for `configure`; nothing here is a claim about current institutional rules."""
    return {
        "ufsc": {
            "base_url": "https://presencial.moodle.ufsc.br",
            "institution": "UFSC (Moodle Presencial)",
            "timezone": "America/Sao_Paulo",
            # No attendance threshold is preset: it is a per-course rule that must carry its source.
        }
    }


def _env(name: str, default: str = "") -> str:
    """Environment value, ignoring empty strings and unsubstituted ${placeholders} left by launchers."""
    v = os.environ.get(name, default).strip()
    return "" if v.startswith("${") else v


def _apply_env_profile(cfg: AppConfig) -> None:
    """A profile can be declared by environment variables (Claude Code plugin `userConfig`, containers).

    MAMCP_BASE_URL (required to activate), MAMCP_PROFILE (default "default"), MAMCP_TIMEZONE,
    MAMCP_INSTITUTION, MAMCP_ATTEST (comma list of services), MAMCP_ATTENDANCE_MIN_PERCENT / _RULE_SOURCE.
    Tokens come from MOODLE_TOKEN_<PROFILE>_<SERVICE>. An explicit config file profile of the same name wins.
    """
    url = _env("MAMCP_BASE_URL")
    if not url:
        return
    name = validate_profile_name(_env("MAMCP_PROFILE") or "default")
    if name in cfg.profiles:
        return
    cfg.profiles[name] = ProfileSettings(
        name=name,
        base_url=normalize_base_url(url),
        institution=_env("MAMCP_INSTITUTION") or name,
        timezone=_env("MAMCP_TIMEZONE") or "UTC",
        attendance_min_percent=_env("MAMCP_ATTENDANCE_MIN_PERCENT") or None,
        attendance_rule_source=_env("MAMCP_ATTENDANCE_RULE_SOURCE") or None,
        attested_services=[s.strip() for s in _env("MAMCP_ATTEST").split(",") if s.strip()],
    )
    cfg.default_profile = cfg.default_profile or name


@dataclass
class AppConfig:
    profiles: dict[str, ProfileSettings] = field(default_factory=dict)
    default_profile: str | None = None
    path: Path | None = None

    def profile(self, name: str | None = None) -> ProfileSettings:
        name = name or self.default_profile
        if not self.profiles:
            raise NotConfiguredError(
                "No Moodle profile configured.",
                hint="Run `moodle-academic-mcp configure` (or `uvx --from git+https://github.com/matheussilvano/"
                "moodle-mcp moodle-academic-mcp configure`) in a terminal, or install the Claude Code plugin, which "
                "asks for the URL and token itself. Never paste the token into the chat.",
            )
        if name is None:
            if len(self.profiles) == 1:
                return next(iter(self.profiles.values()))
            raise ConfigError(
                "Several profiles configured; pass profile=<name>.",
                details={"profiles": sorted(self.profiles)},
            )
        if name not in self.profiles:
            raise ConfigError(f"Unknown profile {name!r}.", details={"profiles": sorted(self.profiles)})
        return self.profiles[name]

    @classmethod
    def load(cls, path: Path | None = None) -> AppConfig:
        path = path or Path(os.environ.get("MAMCP_CONFIG", config_dir() / "config.toml"))
        cfg = cls(path=path)
        if path.exists():
            try:
                raw = tomllib.loads(path.read_text("utf-8"))
            except tomllib.TOMLDecodeError as e:
                raise ConfigError(f"Invalid config file {path}: {e}") from e
            for name, d in (raw.get("profiles") or {}).items():
                cfg.profiles[name] = _profile_from_dict(name, d)
            cfg.default_profile = raw.get("default_profile")
        _apply_env_profile(cfg)
        return cfg

    def save(self) -> None:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        if self.default_profile:
            lines.append(f'default_profile = "{self.default_profile}"\n')
        for name, p in self.profiles.items():
            lines.append(f"\n[profiles.{name}]")
            lines.append(f'base_url = "{p.base_url}"')
            lines.append(f'institution = "{p.institution}"')
            lines.append(f'timezone = "{p.timezone}"')
            if p.allow_insecure_http:
                lines.append("allow_insecure_http = true")
            if p.allow_private_networks:
                lines.append("allow_private_networks = true")
            if p.extra_hosts:
                lines.append("extra_hosts = [" + ", ".join(f'"{h}"' for h in p.extra_hosts) + "]")
            if p.attendance_min_percent:
                lines.append(f'attendance_min_percent = "{p.attendance_min_percent}"')
            if p.attested_services:
                lines.append("attested_services = [" + ", ".join(f'"{s}"' for s in p.attested_services) + "]")
            if p.attendance_rule_source:
                lines.append(f'attendance_rule_source = "{p.attendance_rule_source}"')
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)


# --------------------------------------------------------------------------- local token storage
class LocalTokenStore:
    """Tokens of the local user: env var > keyring > private file. Never logged."""

    def __init__(self, directory: Path | None = None, *, use_keyring: bool | None = None):
        self.dir = directory or config_dir()
        self.file = self.dir / "secrets.json"
        self._use_keyring = self._keyring_available() if use_keyring is None else use_keyring

    @staticmethod
    def _keyring_available() -> bool:
        if os.environ.get("MAMCP_NO_KEYRING"):
            return False
        try:
            import keyring
            from keyring.backends.fail import Keyring as FailKeyring

            return not isinstance(keyring.get_keyring(), FailKeyring)
        except Exception:
            return False

    @staticmethod
    def env_name(profile: str, service: str) -> str:
        return f"MOODLE_TOKEN_{profile.upper().replace('-', '_')}_{service.upper().replace('-', '_')}"

    def _read_file(self) -> dict[str, str]:
        if not self.file.exists():
            return {}
        return json.loads(self.file.read_text("utf-8"))

    def _write_file(self, data: dict[str, str]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.chmod(self.file, 0o600)

    def get(self, profile: str, service: str) -> str | None:
        v = _env(self.env_name(profile, service)) or None
        if not v and self._use_keyring:
            try:
                import keyring

                v = keyring.get_password(KEYRING_SERVICE, f"{profile}:{service}")
            except Exception:
                v = None
        if not v:
            v = self._read_file().get(f"{profile}:{service}")
        if v:
            register_secret(v)
        return v

    def set(self, profile: str, service: str, token: str) -> str:
        """Store a token; returns where it went ('keyring' or 'file')."""
        register_secret(token)
        if self._use_keyring:
            try:
                import keyring

                keyring.set_password(KEYRING_SERVICE, f"{profile}:{service}", token)
                data = self._read_file()
                if data.pop(f"{profile}:{service}", None) is not None:
                    self._write_file(data)
                return "keyring"
            except Exception:
                pass
        data = self._read_file()
        data[f"{profile}:{service}"] = token
        self._write_file(data)
        return "file"

    def delete(self, profile: str, service: str) -> None:
        if self._use_keyring:
            try:
                import keyring

                keyring.delete_password(KEYRING_SERVICE, f"{profile}:{service}")
            except Exception:
                pass
        data = self._read_file()
        if data.pop(f"{profile}:{service}", None) is not None:
            self._write_file(data)

    def services(self, profile: str) -> list[str]:
        found = {s for s in SERVICES if self.get(profile, s)}
        prefix = f"{profile}:"
        found |= {k[len(prefix) :] for k in self._read_file() if k.startswith(prefix)}
        for k in os.environ:
            m = re.fullmatch(rf"MOODLE_TOKEN_{re.escape(profile.upper().replace('-', '_'))}_(\w+)", k)
            if m:
                found.add(m.group(1).lower())
        return sorted(found)
