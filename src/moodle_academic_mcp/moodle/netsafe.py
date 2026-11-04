"""Destination validation (SSRF defence) and safe redirect handling for downloads."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from ..errors import UnsafeDestinationError

_METADATA_HOSTS = {"metadata.google.internal", "metadata", "instance-data"}


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10"))
    )


async def resolve_ips(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({ipaddress.ip_address(i[4][0]) for i in infos}, key=str)


async def check_destination(
    url: str, *, allowed_hosts: set[str], allow_private: bool, allow_insecure_http: bool
) -> None:
    """Raise unless `url` points at an allowed host and (unless allow_private) a public address."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in {"https", "http"}:
        raise UnsafeDestinationError(f"Scheme {parts.scheme!r} is not allowed.")
    if parts.scheme == "http" and not allow_insecure_http:
        raise UnsafeDestinationError(
            "Plain HTTP is not allowed (set allow_insecure_http only for local development)."
        )
    if parts.username or parts.password:
        raise UnsafeDestinationError("Credentials in URLs are not allowed.")
    if host in _METADATA_HOSTS:
        raise UnsafeDestinationError("Metadata hosts are blocked.")
    if host not in {h.lower() for h in allowed_hosts}:
        raise UnsafeDestinationError(
            f"Host {host!r} is not one of the configured Moodle hosts.",
            hint="Tokens are only ever sent to the Moodle host of the profile.",
        )
    if not allow_private:
        try:
            ips = await resolve_ips(host)
        except OSError as e:
            raise UnsafeDestinationError(f"Cannot resolve {host!r}: {e}") from e
        for ip in ips:
            if _is_blocked_ip(ip):
                raise UnsafeDestinationError(
                    f"{host!r} resolves to a private/loopback/link-local address.",
                    hint="Self-hosted private Moodle instances must be allowed explicitly by the administrator "
                    "(allow_private_networks = true on the profile).",
                )
