"""Encryption of Moodle tokens at rest in hosted mode. The key lives outside the database."""

from __future__ import annotations

import json
import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from .errors import ConfigError


class Vault:
    """Fernet (AES-128-CBC + HMAC). Several comma-separated keys allow rotation: first encrypts, all decrypt."""

    def __init__(self, keys: str):
        parts = [k.strip() for k in keys.split(",") if k.strip()]
        if not parts:
            raise ConfigError(
                "MAMCP_ENCRYPTION_KEY is required in hosted mode.",
                hint="Generate one: python -c 'from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())'",
            )
        try:
            self._f = MultiFernet([Fernet(k.encode()) for k in parts])
        except (ValueError, TypeError) as e:
            raise ConfigError("Invalid MAMCP_ENCRYPTION_KEY (must be url-safe base64 of 32 bytes).") from e

    @classmethod
    def from_env(cls) -> Vault:
        return cls(os.environ.get("MAMCP_ENCRYPTION_KEY", ""))

    def seal(self, user_key: str, profile: str, service: str, token: str) -> bytes:
        # Binding the owner into the plaintext defeats row-swapping between users.
        blob = json.dumps({"u": user_key, "p": profile, "s": service, "t": token}).encode()
        return self._f.encrypt(blob)

    def open(self, user_key: str, profile: str, service: str, ciphertext: bytes) -> str | None:
        try:
            d = json.loads(self._f.decrypt(ciphertext))
        except (InvalidToken, ValueError):
            return None
        if (d.get("u"), d.get("p"), d.get("s")) != (user_key, profile, service):
            return None
        return d.get("t")

    def rotate(self, ciphertext: bytes) -> bytes:
        return self._f.rotate(ciphertext)
