"""Authentication and client identity.

Two credential types with deliberately different treatment:

API keys are 256-bit values *we* generate, so they have full entropy and
are not guessable. They are verified with SHA-256 and a constant-time compare.

The admin password is chosen by a human, so it has low entropy and is
vulnerable to offline cracking if the hash leaks. It uses Argon2id.

Using Argon2 for API keys would be a mistake dressed as caution: it adds
~50 ms of deliberate CPU burn to *every* authenticated request, which is a
denial-of-service amplifier an attacker triggers for free by sending garbage
keys. Slow hashing defends low-entropy secrets against offline attack. A
random 256-bit key has nothing to defend, there is no dictionary for it.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
from dataclasses import dataclass
from enum import StrEnum

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from veilix.core.errors import AuthenticationRequiredError

_hasher = PasswordHasher()


class IdentityKind(StrEnum):
    """How a caller was identified. Used as a bounded metric label."""

    ANONYMOUS = "anonymous"
    API_KEY = "api_key"


@dataclass(frozen=True, slots=True)
class ClientIdentity:
    """Who is calling, for rate-limiting purposes only.

    ``bucket`` is the rate-limit key component. For anonymous callers it is a
    salted HMAC of the IP that the caller can neither predict nor reverse
    (ADR-0003); for API-key callers it is a digest of the key. Neither is
    stored beyond the limit window, and neither appears in a log or a metric
    label.

    The raw IP is deliberately *not* a field on this object. Making it
    unavailable downstream is cheaper than reviewing every future line of code
    that might otherwise log it.
    """

    kind: IdentityKind
    bucket: str

    @property
    def is_authenticated(self) -> bool:
        return self.kind is IdentityKind.API_KEY


def hash_api_key(key: str) -> str:
    """SHA-256 hex digest of an API key, for storage and comparison."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    """Generate a new API key with 256 bits of entropy."""
    return f"vlx_{secrets.token_urlsafe(32)}"


def verify_api_key(presented: str, allowed_digests: frozenset[str]) -> bool:
    """Check a presented key against configured digests in constant time.

    ``compare_digest`` is used per candidate so that response timing does not
    reveal how much of a digest matched. The loop runs to completion rather
    than short-circuiting on the first match, so timing does not leak the
    *position* of a matching key either.
    """
    if not presented or not allowed_digests:
        return False

    candidate = hash_api_key(presented)
    matched = False
    for digest in allowed_digests:
        if hmac.compare_digest(candidate, digest):
            matched = True
    return matched


def hash_password(password: str) -> str:
    """Argon2id hash of a human-chosen password."""
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a password against an Argon2 hash.

    Returns False on any verification failure rather than propagating, so a
    malformed hash in configuration behaves as "no access" instead of as a
    500 that an attacker can use to probe configuration state.
    """
    if not stored_hash:
        return False
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, ValueError, TypeError):
        return False


def verify_admin(
    username: str,
    password: str,
    *,
    expected_username: str,
    password_hash: str,
) -> bool:
    """Verify admin credentials.

    The password is always verified, even when the username is wrong. Skipping
    the Argon2 computation on a username mismatch would make wrong-username
    responses measurably faster than wrong-password ones, handing an attacker a
    way to enumerate the valid username before starting on the password.
    """
    username_ok = hmac.compare_digest(username.encode(), expected_username.encode())
    password_ok = verify_password(password, password_hash)
    return username_ok and password_ok


def require_admin_or_raise(
    username: str,
    password: str,
    *,
    expected_username: str,
    password_hash: str,
) -> None:
    if not password_hash:
        # Refusing outright beats defaulting to open. A deployment that forgot
        # to configure admin credentials gets a locked door, not a lobby.
        raise AuthenticationRequiredError("Admin access is not configured on this instance.")
    if not verify_admin(
        username,
        password,
        expected_username=expected_username,
        password_hash=password_hash,
    ):
        raise AuthenticationRequiredError("Invalid administrator credentials.")


# ---------------------------------------------------------------------------
# Client IP extraction
# ---------------------------------------------------------------------------


def client_ip_from_headers(
    *,
    forwarded_for: str | None,
    peer_ip: str | None,
    trust_proxy: bool,
) -> str:
    """Determine the client IP, treating ``X-Forwarded-For`` as untrusted input.

    This is a small function guarding a large mistake. ``X-Forwarded-For`` is
    an ordinary request header: any client can send one. If it is honoured
    unconditionally, an attacker sets a fresh value per request and every
    rate-limit bucket becomes theirs to choose, the limiter still runs, still
    reports numbers, and no longer limits anything.

    So the header is read only when the deployment states that it sits
    behind a trusted proxy, and the *first* entry is taken, which is the
    original client as appended by our own proxy. Taking the last entry is a
    common inversion of this that trusts whatever the attacker appended.

    Returns a sentinel rather than raising when nothing is available: an
    unidentifiable client should share one heavily-used bucket, not bypass the
    limiter through an error path.
    """
    if trust_proxy and forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if _is_valid_ip(first):
            return first
    if peer_ip and _is_valid_ip(peer_ip):
        return peer_ip
    return "unknown"


def _is_valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True
