"""URL checks for search results, which are attacker-influenced content.

Anyone who can rank for a query controls the URLs we hand to a browser, and the
URLs we sign for the image proxy.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Names under these never resolve on the public internet.
_INTERNAL_SUFFIXES = (
    ".local",
    ".localhost",
    ".localdomain",
    ".internal",
    ".intranet",
    ".lan",
    ".home",
    ".home.arpa",
    ".corp",
    ".private",
    ".test",
    ".invalid",
    ".example",
)

MAX_URL_LENGTH = 2048


def is_safe_web_url(url: str) -> bool:
    """Whether a URL is safe to render as a link.

    Allowlisted rather than denylisted, so `javascript:`, `data:` and anything
    invented later are all rejected without needing to be enumerated.
    """
    if not url or len(url) > MAX_URL_LENGTH:
        return False
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    return parts.scheme.lower() in _ALLOWED_SCHEMES and bool(parts.netloc)


def is_safe_to_proxy(url: str) -> bool:
    """Whether a URL may be signed for the image proxy.

    Stricter than `is_safe_web_url`: signing makes SearXNG fetch the URL, and
    SearXNG has internet egress. A link is only fetched if someone clicks it.

    Does not catch a public hostname whose DNS points somewhere private. We
    cannot resolve to check, because the API container has no external DNS
    (ADR-0004). Closing that needs an egress policy on SearXNG's side.
    """
    if not is_safe_web_url(url):
        return False

    host = urlsplit(url.strip()).hostname
    if not host:
        return False
    host = host.lower().rstrip(".")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return _is_public_hostname(host)

    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or address.is_multicast
    )


def _is_public_hostname(host: str) -> bool:
    """Reject names that cannot belong to a public host.

    Blocking private IP literals is not enough under Docker: its embedded DNS
    resolves `valkey` and `api`, so those arrive here as hostnames. Every
    routable public name has a dot; container service names do not.
    """
    if not host or " " in host:
        return False
    if "." not in host:
        return False
    return not host.endswith(_INTERNAL_SUFFIXES)
