"""Short-lived, identity-free result cache.

The cache key is a hash of normalised query parameters and nothing else. That
has one deliberate consequence and one honest cost.

The consequence: entries are shared across every user, because there is no
identity in the key for them to be partitioned by. That is what makes caching
compatible with the privacy model at all.

The cost: a shared cache is a timing side channel. Anyone able to measure
response latency can distinguish a hit from a miss and infer that *somebody*
searched a term within the TTL. They learn nothing about who. The TTL bounds
the window, the cache can be switched off entirely, and docs/privacy.md §7
states this plainly rather than leaving it to be discovered.
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol

import orjson
from redis.asyncio import Redis

from veilix.core.logging import get_logger
from veilix.core.telemetry import cache_operations_total

log = get_logger(__name__)

_KEY_PREFIX = "sc:"
_SCHEMA_VERSION = "v1"


class ResultCache(Protocol):
    """Cache interface, so callers never branch on whether caching is on."""

    async def get(self, fingerprint: str) -> dict[str, Any] | None: ...

    async def set(self, fingerprint: str, payload: dict[str, Any]) -> None: ...


def cache_key(fingerprint: str) -> str:
    """Hash a query fingerprint into an opaque cache key.

        Hashed instead of stored in the clear for two reasons. It keeps keys a
        fixed short length regardless of query length, and it means a casual
        `KEYS *` against the datastore shows opaque digests instead of a readable
        list of what people have been searching for. It is not a security control
    , anyone who can guess a query can compute its digest and confirm it, and
        docs/privacy.md §9 says so, but it removes the accidental disclosure.

        The schema version is part of the digest so that a change to the cached
        payload shape cannot collide with entries written by an older build.
    """
    digest = hashlib.sha256(f"{_SCHEMA_VERSION}|{fingerprint}".encode()).hexdigest()
    return f"{_KEY_PREFIX}{digest[:32]}"


class ValkeyResultCache:
    """Valkey-backed cache with a short TTL."""

    def __init__(self, redis: Redis, *, ttl_s: int = 300) -> None:
        self._redis = redis
        self._ttl_s = ttl_s

    async def get(self, fingerprint: str) -> dict[str, Any] | None:
        try:
            raw = await self._redis.get(cache_key(fingerprint))
        except Exception as exc:
            # A cache is an optimisation. Losing it degrades latency, and must
            # never degrade correctness or availability.
            cache_operations_total.labels(outcome="error").inc()
            log.warning("cache_read_failed", error_type=type(exc).__name__)
            return None

        if raw is None:
            cache_operations_total.labels(outcome="miss").inc()
            return None

        try:
            payload: dict[str, Any] = orjson.loads(raw)
        except orjson.JSONDecodeError:
            # Corrupt or stale-schema entry. Treat as a miss rather than
            # failing the request.
            cache_operations_total.labels(outcome="error").inc()
            return None

        cache_operations_total.labels(outcome="hit").inc()
        return payload

    async def set(self, fingerprint: str, payload: dict[str, Any]) -> None:
        if self._ttl_s <= 0:
            return
        try:
            await self._redis.set(cache_key(fingerprint), orjson.dumps(payload), ex=self._ttl_s)
        except Exception as exc:
            log.warning("cache_write_failed", error_type=type(exc).__name__)


class NullResultCache:
    """No-op cache used when caching is disabled.

    A null object instead of an ``if self._cache is not None`` scattered
    through the search service. It also makes "caching off" a configuration
    that is exercised by the same code path as caching on, instead of a
    branch that is never tested.
    """

    async def get(self, fingerprint: str) -> dict[str, Any] | None:
        cache_operations_total.labels(outcome="disabled").inc()
        return None

    async def set(self, fingerprint: str, payload: dict[str, Any]) -> None:
        return None
