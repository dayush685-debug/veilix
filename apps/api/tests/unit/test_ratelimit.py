"""Rate limiter behaviour and its privacy properties (ADR-0003)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from veilix.core.security import IdentityKind
from veilix.infrastructure.ratelimit import RateLimiter


@pytest.fixture
def limiter(redis: Any) -> RateLimiter:
    return RateLimiter(
        redis,
        salt_seed="fixed-seed-for-tests",
        anonymous_limit=5,
        api_key_limit=50,
        window_s=60,
        enabled=True,
    )


class TestBucketPrivacy:
    """The properties that make counting-without-remembering true."""

    def test_bucket_does_not_contain_the_ip(self, limiter: RateLimiter) -> None:
        bucket = limiter.bucket_for_ip("203.0.113.42")
        assert "203.0.113.42" not in bucket
        assert "203" not in bucket or len(bucket) == 16

    def test_bucket_is_stable_within_a_day(self, limiter: RateLimiter) -> None:
        # Must be stable, or the limit would never be reached.
        moment = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
        later = datetime(2026, 8, 30, 23, 59, tzinfo=UTC)
        assert limiter.bucket_for_ip("1.2.3.4", moment) == limiter.bucket_for_ip("1.2.3.4", later)

    def test_bucket_changes_across_days(self, limiter: RateLimiter) -> None:
        """The core unlinkability property.

        After rotation, yesterday's keys cannot be tied to any address — not by
        the operator, not by whoever obtains the datastore. This is what makes
        cross-day correlation not merely forbidden but uncomputable.
        """
        day1 = limiter.bucket_for_ip("1.2.3.4", datetime(2026, 8, 30, tzinfo=UTC))
        day2 = limiter.bucket_for_ip("1.2.3.4", datetime(2026, 8, 31, tzinfo=UTC))
        assert day1 != day2

    def test_different_ips_get_different_buckets(self, limiter: RateLimiter) -> None:
        at = datetime(2026, 8, 30, tzinfo=UTC)
        buckets = {limiter.bucket_for_ip(f"10.0.0.{i}", at) for i in range(50)}
        assert len(buckets) == 50, "bucket collisions would merge unrelated clients"

    def test_salt_seed_changes_all_buckets(self, redis: Any) -> None:
        at = datetime(2026, 8, 30, tzinfo=UTC)
        a = RateLimiter(redis, salt_seed="seed-a").bucket_for_ip("1.2.3.4", at)
        b = RateLimiter(redis, salt_seed="seed-b").bucket_for_ip("1.2.3.4", at)
        assert a != b


class TestIdentity:
    def test_anonymous_identity_from_ip(self, limiter: RateLimiter) -> None:
        identity = limiter.identify(client_ip="1.2.3.4", api_key=None)
        assert identity.kind is IdentityKind.ANONYMOUS
        assert not identity.is_authenticated

    def test_api_key_identity_is_independent_of_ip(self, limiter: RateLimiter) -> None:
        # A legitimate integration behind a changing address keeps one budget.
        a = limiter.identify(client_ip="1.2.3.4", api_key="key-abc")
        b = limiter.identify(client_ip="5.6.7.8", api_key="key-abc")
        assert a.bucket == b.bucket
        assert a.kind is IdentityKind.API_KEY

    def test_api_key_bucket_is_not_the_key(self, limiter: RateLimiter) -> None:
        identity = limiter.identify(client_ip="1.2.3.4", api_key="super-secret-key")
        assert "super-secret-key" not in identity.bucket

    def test_authenticated_callers_get_the_higher_limit(self, limiter: RateLimiter) -> None:
        anon = limiter.identify(client_ip="1.2.3.4", api_key=None)
        keyed = limiter.identify(client_ip="1.2.3.4", api_key="k")
        assert limiter.limit_for(anon) == 5
        assert limiter.limit_for(keyed) == 50


class TestEnforcement:
    async def test_allows_up_to_the_limit_then_blocks(self, limiter: RateLimiter) -> None:
        identity = limiter.identify(client_ip="1.2.3.4", api_key=None)

        decisions = [await limiter.check(identity) for _ in range(5)]
        assert all(d.allowed for d in decisions), "first 5 must be allowed"

        blocked = await limiter.check(identity)
        assert blocked.allowed is False
        assert blocked.retry_after_s > 0
        assert blocked.limit == 5

    async def test_remaining_counts_down(self, limiter: RateLimiter) -> None:
        identity = limiter.identify(client_ip="9.9.9.9", api_key=None)
        first = await limiter.check(identity)
        second = await limiter.check(identity)
        assert first.remaining > second.remaining

    async def test_separate_clients_do_not_share_a_budget(self, limiter: RateLimiter) -> None:
        a = limiter.identify(client_ip="1.1.1.1", api_key=None)
        b = limiter.identify(client_ip="2.2.2.2", api_key=None)

        for _ in range(6):
            await limiter.check(a)

        assert (await limiter.check(b)).allowed is True

    async def test_disabled_limiter_always_allows(self, redis: Any) -> None:
        limiter = RateLimiter(redis, salt_seed="s", anonymous_limit=1, window_s=60, enabled=False)
        identity = limiter.identify(client_ip="1.2.3.4", api_key=None)
        for _ in range(10):
            assert (await limiter.check(identity)).allowed is True

    async def test_fails_open_when_the_datastore_is_unreachable(self) -> None:
        """A cache outage must not become a total outage.

        Failing closed would turn every Valkey hiccup into a site-wide 429.
        The trade is deliberate and bounded by how long Valkey stays down; it
        is logged loudly rather than silently.
        """

        class BrokenRedis:
            def register_script(self, _script: str) -> Any:
                async def run(**_kwargs: Any) -> Any:
                    raise ConnectionError("valkey is down")

                return run

        limiter = RateLimiter(
            BrokenRedis(),  # type: ignore[arg-type]
            salt_seed="s",
            anonymous_limit=1,
            window_s=60,
        )
        identity = limiter.identify(client_ip="1.2.3.4", api_key=None)

        for _ in range(20):
            decision = await limiter.check(identity)
            assert decision.allowed is True
