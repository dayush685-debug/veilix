"""Privacy-preserving rate limiting.

Implements ADR-0003. Two ideas carry the module:

**The identity is a rotating-salt HMAC, never an IP.** The raw address is a
function argument and nothing else — it is never written to Valkey, a log, or
a metric label. Because the salt rotates daily and is derived rather than
stored, yesterday's buckets cannot be linked to any address by anyone,
including whoever holds the database. The limiter can count a client; it
cannot remember one.

**The window slides.** A fixed window lets a client spend its full budget in
the last second of one window and again in the first second of the next, so
the real burst capacity is double the configured limit. The weighted
two-window estimate below removes that at the cost of one extra counter.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from redis.asyncio import Redis

from veilix.core.logging import get_logger
from veilix.core.security import ClientIdentity, IdentityKind
from veilix.core.telemetry import ratelimit_events_total

log = get_logger(__name__)

# Atomic increment-and-read of the current and previous window counters.
#
# Server-side because the alternative is read-then-write from the client,
# which races: two concurrent requests both read N, both write N+1, and one
# request is served for free. Under exactly the burst the limiter exists to
# stop, that race is at its most likely.
_SLIDING_WINDOW_LUA = """
local current_key  = KEYS[1]
local previous_key = KEYS[2]
local window       = tonumber(ARGV[1])
local elapsed      = tonumber(ARGV[2])

local current = redis.call('INCR', current_key)
if current == 1 then
  -- First hit in this window: expire after two windows so the counter is
  -- still readable as "previous" while the next window is active.
  redis.call('EXPIRE', current_key, window * 2)
end

local previous = tonumber(redis.call('GET', previous_key)) or 0

-- Weight the previous window by the fraction of it still inside the sliding
-- window. At 25% through the current window, 75% of the previous one counts.
local weight = 1.0 - (elapsed / window)
local estimate = current + (previous * weight)

return {current, math.floor(estimate * 1000)}
"""


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_s: int


class RateLimiter:
    """Sliding-window limiter keyed by an unlinkable client identifier."""

    def __init__(
        self,
        redis: Redis,
        *,
        salt_seed: str,
        anonymous_limit: int = 60,
        api_key_limit: int = 600,
        window_s: int = 60,
        enabled: bool = True,
    ) -> None:
        self._redis = redis
        self._salt_seed = salt_seed
        self._anonymous_limit = anonymous_limit
        self._api_key_limit = api_key_limit
        self._window_s = window_s
        self._enabled = enabled
        self._script = redis.register_script(_SLIDING_WINDOW_LUA)

    # -- identity ----------------------------------------------------------

    def _daily_salt(self, now: datetime | None = None) -> bytes:
        """Derive today's salt from the configured seed and the UTC date.

        Derived rather than stored, so there is no salt at rest to steal and
        no rotation job to fail. Deriving it also means every replica computes
        the same salt for the same day, which a randomly generated per-process
        salt would not — each replica would otherwise enforce its own separate
        limit for the same client.
        """
        day = (now or datetime.now(UTC)).strftime("%Y-%m-%d")
        return hashlib.sha256(f"{self._salt_seed}:{day}".encode()).digest()

    def bucket_for_ip(self, client_ip: str, now: datetime | None = None) -> str:
        """Unlinkable bucket identifier for an anonymous client.

        Truncated to 16 hex characters (64 bits). Enough that accidental
        collisions between concurrent clients are negligible, short enough to
        keep keys small. Truncation is not a weakness here — the value is
        already unpredictable without the salt, and shortening it only removes
        information.
        """
        digest = hmac.new(self._daily_salt(now), client_ip.encode(), hashlib.sha256)
        return digest.hexdigest()[:16]

    def identify(
        self,
        *,
        client_ip: str,
        api_key: str | None,
        now: datetime | None = None,
    ) -> ClientIdentity:
        """Build the caller's identity for limiting purposes.

        Callers presenting a verified API key are bucketed by a digest of the
        key rather than by address, so a legitimate integration behind a
        changing IP keeps one budget, and several users behind one corporate
        NAT do not share one.
        """
        if api_key:
            digest = hashlib.sha256(api_key.encode()).hexdigest()[:16]
            return ClientIdentity(kind=IdentityKind.API_KEY, bucket=digest)
        return ClientIdentity(
            kind=IdentityKind.ANONYMOUS, bucket=self.bucket_for_ip(client_ip, now)
        )

    # -- limiting ----------------------------------------------------------

    def limit_for(self, identity: ClientIdentity) -> int:
        return self._api_key_limit if identity.is_authenticated else self._anonymous_limit

    async def check(self, identity: ClientIdentity) -> RateLimitDecision:
        """Record a request and decide whether it is allowed.

        **Fails open.** If Valkey is unreachable the request is permitted, and
        that is a deliberate trade: a cache outage would otherwise become a
        total outage, turning a degraded dependency into a hard one. The
        opposite choice — fail closed — converts every Valkey hiccup into a
        site-wide 429. The exposure window is bounded by how long Valkey stays
        down, and the failure is logged loudly so it is not silent.
        """
        limit = self.limit_for(identity)

        if not self._enabled:
            return RateLimitDecision(True, limit, limit, 0)

        now = time.time()
        window_start = int(now // self._window_s) * self._window_s
        elapsed = now - window_start

        current_key = f"rl:{identity.bucket}:{window_start}"
        previous_key = f"rl:{identity.bucket}:{window_start - self._window_s}"

        try:
            raw = await self._script(
                keys=[current_key, previous_key],
                args=[self._window_s, elapsed],
            )
            estimate = int(raw[1]) / 1000.0
        except Exception as exc:
            log.error(
                "ratelimit_backend_unavailable",
                error_type=type(exc).__name__,
                action="failing_open",
            )
            return RateLimitDecision(True, limit, limit, 0)

        allowed = estimate <= limit
        remaining = max(0, int(limit - estimate))
        retry_after = 0 if allowed else max(1, int(self._window_s - elapsed))

        ratelimit_events_total.labels(
            identity=identity.kind.value,
            decision="allowed" if allowed else "blocked",
        ).inc()

        return RateLimitDecision(allowed, limit, remaining, retry_after)
