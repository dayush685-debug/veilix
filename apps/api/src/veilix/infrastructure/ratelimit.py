"""Rate limiting keyed by a rotating-salt HMAC of the client IP (ADR-0003).

The raw address is a function argument and nothing else. It never reaches
Valkey, a log, or a metric label, and the daily salt is derived rather than
stored, so yesterday's buckets cannot be tied back to an address.
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

# Server-side so the increment and read are atomic. Read-then-write from the
# client races under exactly the burst this exists to stop.
_SLIDING_WINDOW_LUA = """
local current_key  = KEYS[1]
local previous_key = KEYS[2]
local window       = tonumber(ARGV[1])
local elapsed      = tonumber(ARGV[2])

local current = redis.call('INCR', current_key)
if current == 1 then
  -- Two windows, so this counter is still readable as "previous" during the
  -- next one.
  redis.call('EXPIRE', current_key, window * 2)
end

local previous = tonumber(redis.call('GET', previous_key)) or 0

-- 25% into the current window, 75% of the previous one still counts.
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
        """Today's salt, derived from the seed and the UTC date.

        Derived, not stored: no salt at rest to steal, no rotation job to fail,
        and every replica computes the same value for a given day.
        """
        day = (now or datetime.now(UTC)).strftime("%Y-%m-%d")
        return hashlib.sha256(f"{self._salt_seed}:{day}".encode()).digest()

    def bucket_for_ip(self, client_ip: str, now: datetime | None = None) -> str:
        """Unlinkable bucket identifier for an anonymous client.

        64 bits is plenty: the value is already unpredictable without the salt,
        so truncating only discards information.
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
        """Identify the caller for limiting purposes.

        Verified API keys bucket by key digest, not address, so an integration
        behind a changing IP keeps one budget and users behind one NAT do not
        share one.
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

        Fails open. Failing closed would turn any Valkey hiccup into a
        site-wide 429, so a degraded dependency stays degraded instead of
        becoming a hard one. Logged at error level.
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
