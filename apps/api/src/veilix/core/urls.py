"""URL safety checks for attacker-influenced content.

Every URL in a search result was authored by a third party. Anyone who can
rank for a query chooses those bytes, so result URLs are untrusted input that
happens to arrive by a trusted route. Two findings depend on this module:

**SF-005** — a ``javascript:`` or ``data:`` URL in a result's ``url`` field
becomes a clickable link in someone's browser. Scheme allowlisting stops it at
the API boundary, before the frontend ever has to be careful.

**SF-003** — the API signs image URLs for SearXNG's proxy, which makes it a
signing oracle for whatever appears in results. A result carrying
``img_src: http://169.254.169.254/...`` would otherwise be signed and fetched
by SearXNG, which does have internet egress.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

# Only schemes that are meaningful for a web result and safe to put in an href.
_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Magnet links are legitimate in the torrent category but must never be
# rendered as ordinary links or fed to the image proxy.
_KNOWN_NON_WEB_SCHEMES = frozenset({"magnet"})


def is_safe_web_url(url: str) -> bool:
    """Whether a URL is safe to expose as a clickable link.

    Rejects everything that is not http or https. ``javascript:``, ``data:``,
    ``vbscript:`` and ``file:`` are the ones that matter; the allowlist covers
    them and every future scheme nobody has thought of yet, which is the point
    of allowlisting rather than blocking known-bad values.
    """
    if not url or len(url) > 2048:
        return False
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    return parts.scheme.lower() in _ALLOWED_SCHEMES and bool(parts.netloc)


def is_known_scheme(url: str) -> bool:
    """Whether the scheme is one we recognise, web or otherwise."""
    try:
        scheme = urlsplit(url.strip()).scheme.lower()
    except ValueError:
        return False
    return scheme in _ALLOWED_SCHEMES or scheme in _KNOWN_NON_WEB_SCHEMES


def is_safe_to_proxy(url: str) -> bool:
    """Whether a URL may be signed for the image proxy.

    Stricter than :func:`is_safe_web_url`, because signing a URL causes a
    server with internet egress to *fetch* it, whereas a link is only fetched
    if a person clicks it.

    Rejected: non-http(s) schemes, and hosts that are literal IP addresses in
    private, loopback, link-local, reserved, or unspecified ranges. That covers
    the cloud metadata endpoint at 169.254.169.254 and services on the
    container network.

    **The limit of this check, stated rather than implied.** It inspects the
    host as written. A hostname that *resolves* to an internal address defeats
    it, and the API container cannot tell the difference because ADR-0004
    leaves it without external DNS — the same isolation that contains an
    attacker also prevents this function from resolving anything. Closing that
    gap needs an egress policy on the fetching side, which is tracked in
    SF-003. This is a real layer, not a complete one.
    """
    if not is_safe_web_url(url):
        return False

    host = urlsplit(url.strip()).hostname
    if not host:
        return False

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # A hostname, not a literal address. See the caveat above.
        return True

    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or address.is_multicast
    )
