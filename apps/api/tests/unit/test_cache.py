"""Cache behaviour and resilience.

The governing property: a cache is an optimisation, and losing it must
degrade latency without degrading correctness or availability. Every error
path here exists so that a sick Valkey makes search slower, not broken.

The second property is privacy: the cache key must carry no identity, because
that is what makes entries shareable between users and therefore compatible
with the privacy model at all.
"""

from __future__ import annotations

from typing import Any

import orjson
import pytest

from veilix.domain.models import SafeSearch, SearchCategory, SearchQuery, TimeRange
from veilix.infrastructure.cache import (
    NullResultCache,
    ValkeyResultCache,
    cache_key,
)


class BrokenRedis:
    """A Valkey that fails every operation, as an outage would."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error or ConnectionError("valkey is down")

    async def get(self, key: str) -> bytes | None:
        raise self._error

    async def set(self, key: str, value: bytes, ex: int | None = None) -> None:
        raise self._error


class FakeRedis:
    """A minimal working store."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.last_ttl: int | None = None

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: bytes, ex: int | None = None) -> None:
        self.store[key] = value
        self.last_ttl = ex


class TestCacheKey:
    def test_is_deterministic(self) -> None:
        assert cache_key("a|b|c") == cache_key("a|b|c")

    def test_differs_for_different_queries(self) -> None:
        assert cache_key("query one") != cache_key("query two")

    def test_does_not_contain_the_query_in_the_clear(self) -> None:
        """Hashed so a casual `KEYS *` shows digests, not a readable list of
        what people searched for.

        Not a security control - anyone who can guess a query can compute its
        digest and confirm it, which docs/privacy.md §9 states plainly. It
        removes the accidental disclosure, not the deliberate one.
        """
        key = cache_key("something personal|general|1|auto|1||")
        assert "something personal" not in key
        assert key.startswith("sc:")

    def test_is_fixed_length_regardless_of_query_length(self) -> None:
        short, long = cache_key("a"), cache_key("z" * 5000)
        assert len(short) == len(long)


class TestQueryFingerprint:
    def test_identical_queries_share_a_fingerprint(self) -> None:
        a = SearchQuery(text="privacy", category=SearchCategory.GENERAL)
        b = SearchQuery(text="privacy", category=SearchCategory.GENERAL)
        assert a.cache_fingerprint() == b.cache_fingerprint()

    def test_case_and_surrounding_space_do_not_split_the_cache(self) -> None:
        a = SearchQuery(text="Privacy Tools")
        b = SearchQuery(text="  privacy tools  ")
        assert a.cache_fingerprint() == b.cache_fingerprint()

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("category", SearchCategory.NEWS),
            ("page", 2),
            ("language", "de"),
            ("safesearch", SafeSearch.STRICT),
            ("time_range", TimeRange.WEEK),
        ],
    )
    def test_every_parameter_that_changes_results_changes_the_key(
        self, field: str, value: Any
    ) -> None:
        # A parameter left out of the key would serve results for a different
        # request - the classic way a cache starts returning wrong answers.
        base = SearchQuery(text="same")
        variant = SearchQuery(text="same", **{field: value})
        assert base.cache_fingerprint() != variant.cache_fingerprint()

    def test_engine_order_does_not_split_the_cache(self) -> None:
        a = SearchQuery(text="x", engines=("mojeek", "qwant"))
        b = SearchQuery(text="x", engines=("qwant", "mojeek"))
        assert a.cache_fingerprint() == b.cache_fingerprint()

    def test_carries_no_identity_component(self) -> None:
        """The whole basis of the cache being privacy-compatible.

        There is nothing in SearchQuery that identifies a caller, so two
        different users issuing the same search share an entry - which is what
        makes a shared cache possible, and also what creates the timing side
        channel documented in docs/privacy.md §7.
        """
        fingerprint = SearchQuery(text="q").cache_fingerprint()
        assert "ip" not in fingerprint
        assert fingerprint == SearchQuery(text="q").cache_fingerprint()


class TestValkeyCacheResilience:
    async def test_a_read_failure_reads_as_a_miss(self) -> None:
        cache = ValkeyResultCache(BrokenRedis(), ttl_s=300)  # type: ignore[arg-type]
        assert await cache.get("fingerprint") is None

    async def test_a_write_failure_is_swallowed(self) -> None:
        # A failed cache write must not fail the search whose results it was
        # trying to store.
        cache = ValkeyResultCache(BrokenRedis(), ttl_s=300)  # type: ignore[arg-type]
        await cache.set("fingerprint", {"results": []})

    async def test_corrupt_json_reads_as_a_miss(self) -> None:
        redis = FakeRedis()
        redis.store[cache_key("fp")] = b"{not valid json"
        cache = ValkeyResultCache(redis, ttl_s=300)  # type: ignore[arg-type]
        # Treated as a miss rather than raising, so an entry written by an
        # older build cannot break requests after a deploy.
        assert await cache.get("fp") is None

    async def test_a_round_trip_returns_the_payload(self) -> None:
        redis = FakeRedis()
        cache = ValkeyResultCache(redis, ttl_s=120)
        payload = {"results": [{"url": "https://example.com", "title": "t"}]}

        await cache.set("fp", payload)
        assert await cache.get("fp") == payload

    async def test_the_ttl_is_applied_on_write(self) -> None:
        redis = FakeRedis()
        await ValkeyResultCache(redis, ttl_s=42).set("fp", {"a": 1})
        # Bounds the timing side channel's observation window.
        assert redis.last_ttl == 42

    async def test_a_zero_ttl_writes_nothing(self) -> None:
        redis = FakeRedis()
        await ValkeyResultCache(redis, ttl_s=0).set("fp", {"a": 1})
        assert redis.store == {}

    async def test_stored_bytes_are_json_not_pickle(self) -> None:
        """Guards a deliberate choice with a security consequence.

        Pickle would deserialise arbitrary objects from the cache, turning any
        write access to Valkey into code execution in the API process. The
        cache is shared, ephemeral, and reachable by anything on the backend
        network, precisely where that matters.
        """
        redis = FakeRedis()
        await ValkeyResultCache(redis, ttl_s=60).set("fp", {"results": []})
        raw = next(iter(redis.store.values()))
        assert orjson.loads(raw) == {"results": []}


class TestNullCache:
    async def test_always_misses(self) -> None:
        assert await NullResultCache().get("anything") is None

    async def test_writes_are_a_no_op(self) -> None:
        await NullResultCache().set("fp", {"results": []})

    async def test_satisfies_the_same_interface(self) -> None:
        """A null object instead of `if cache is not None` scattered around.

        It also means "caching disabled" exercises the same code path as
        caching enabled, instead of being a branch nothing ever tests.
        """
        null, valkey = NullResultCache(), ValkeyResultCache(FakeRedis())  # type: ignore[arg-type]
        for method in ("get", "set"):
            assert hasattr(null, method) and hasattr(valkey, method)
