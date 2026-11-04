"""Exception hierarchy. Messages never contain credentials (see redact.py)."""

from __future__ import annotations


class MamcpError(Exception):
    """Base class. `code` is a stable machine-readable identifier."""

    code = "error"

    def __init__(self, message: str, *, hint: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details or {}

    def to_dict(self) -> dict:
        out: dict = {"error": self.code, "message": self.message}
        if self.hint:
            out["hint"] = self.hint
        if self.details:
            out["details"] = self.details
        return out


class ConfigError(MamcpError):
    code = "config_error"


class NotConfiguredError(ConfigError):
    code = "not_configured"


class MoodleAPIError(MamcpError):
    code = "moodle_error"

    def __init__(self, message: str, *, errorcode: str | None = None, function: str | None = None, **kw):
        super().__init__(message, **kw)
        self.errorcode = errorcode
        self.function = function
        if errorcode:
            self.details.setdefault("moodle_errorcode", errorcode)
        if function:
            self.details.setdefault("function", function)


class TokenInvalidError(MoodleAPIError):
    code = "token_invalid"


class PermissionDeniedError(MoodleAPIError):
    code = "permission_denied"


class FunctionUnavailableError(MoodleAPIError):
    """The function is not part of the external service the token belongs to (or does not exist)."""

    code = "function_unavailable"


class ServiceUnavailableError(MoodleAPIError):
    code = "service_unavailable"


class NetworkRestrictionError(MamcpError):
    """HTML/login redirect or similar: typically VPN / external-access restriction."""

    code = "network_restriction"


class WriteOutcomeUnknownError(MamcpError):
    """A non-idempotent write timed out or lost its response. Never auto-retried; reconcile with a read."""

    code = "write_outcome_unknown"


class UnsafeDestinationError(MamcpError):
    code = "unsafe_destination"


class UnsupportedError(MamcpError):
    """Feature impossible with the APIs exposed by this Moodle (specific limitation, with a link when possible)."""

    code = "unsupported"


class NotFoundError(MamcpError):
    code = "not_found"


class ValidationError(MamcpError):
    code = "invalid_input"


class AuthError(MamcpError):
    code = "unauthenticated"
