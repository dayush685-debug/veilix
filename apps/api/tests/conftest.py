"""Shared test fixtures.

Tests run against a real application object with fake infrastructure: a
fakeredis instance instead of Valkey, and respx intercepting the SearXNG calls.

Fakes rather than mocks for the datastore, because the rate limiter runs a Lua
script and the cache does real SETEX with TTLs. A mock would assert that we
called the methods we think we call, which proves nothing about whether the
sliding-window arithmetic is right.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import fakeredis.aioredis
import httpx
import pytest
from fastapi import FastAPI

from veilix.core.config import Settings, get_settings
from veilix.infrastructure.cache import ValkeyResultCache
from veilix.infrastructure.circuit_breaker import CircuitBreaker
from veilix.infrastructure.ratelimit import RateLimiter
from veilix.providers.searxng import SearxngProvider
from veilix.services.search_service import SearchService

SEARXNG_BASE = "http://searxng.test:8080"

# A fixed secret so image-proxy signatures are reproducible across runs.
TEST_SECRET = "test-secret-not-used-anywhere-real-0123456789abcdef"  # noqa: S105


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Settings are cached process-wide; tests must not inherit each other's."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        env="testing",
        searxng_url=SEARXNG_BASE,  # type: ignore[arg-type]
        searxng_secret=TEST_SECRET,
        ratelimit_salt_seed="deterministic-seed-for-tests",
        cache_enabled=True,
        cache_ttl_s=60,
        ratelimit_enabled=True,
        ratelimit_requests=5,
        ratelimit_window_s=60,
        ratelimit_apikey_requests=50,
        search_timeout_s=2.0,
        search_max_retries=0,
        metrics_enabled=True,
    )


@pytest.fixture
async def redis() -> AsyncIterator[Any]:
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(follow_redirects=False) as client:
        yield client


@pytest.fixture
def provider(http_client: httpx.AsyncClient) -> SearxngProvider:
    return SearxngProvider(
        http_client,
        base_url=SEARXNG_BASE,
        secret=TEST_SECRET,
        timeout_s=2.0,
        max_retries=0,
    )


@pytest.fixture
def breaker() -> CircuitBreaker:
    return CircuitBreaker(name="test", fail_threshold=3, reset_timeout_s=0.2)


@pytest.fixture
def search_service(provider: SearxngProvider, redis: Any, breaker: CircuitBreaker) -> SearchService:
    return SearchService(provider, ValkeyResultCache(redis, ttl_s=60), breaker)


@pytest.fixture
def rate_limiter(redis: Any, settings: Settings) -> RateLimiter:
    return RateLimiter(
        redis,
        salt_seed=settings.ratelimit_salt_seed,
        anonymous_limit=settings.ratelimit_requests,
        api_key_limit=settings.ratelimit_apikey_requests,
        window_s=settings.ratelimit_window_s,
        enabled=True,
    )


@pytest.fixture
def app(
    settings: Settings,
    provider: SearxngProvider,
    redis: Any,
    breaker: CircuitBreaker,
    search_service: SearchService,
    rate_limiter: RateLimiter,
    http_client: httpx.AsyncClient,
) -> FastAPI:
    """An app wired to fakes, bypassing lifespan.

    ``create_app`` is used so routing, middleware ordering, and exception
    handlers are the real ones, the parts most likely to break. Only the
    dependencies that would need a network are substituted.
    """
    from veilix.main import create_app

    application = create_app(settings)
    application.state.http_client = http_client
    application.state.redis = redis
    application.state.provider = provider
    application.state.breaker = breaker
    application.state.search_service = search_service
    application.state.rate_limiter = rate_limiter
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """HTTP client bound to the app in-process.

    ASGITransport instead of a live server: no port binding, no startup race,
    and lifespan is skipped so the fakes above are not overwritten by real
    connections.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Response fixtures, shaped from a real probe, not invented
# ---------------------------------------------------------------------------


def searxng_response(
    *,
    results: list[dict[str, Any]] | None = None,
    unresponsive: list[list[str]] | None = None,
) -> dict[str, Any]:
    """A SearXNG JSON payload.

    Field names and value shapes were taken from a live instance, including
    the details that matter: ``engines`` is a list, ``publishedDate`` may be
    null, and ``unresponsive_engines`` is a list of ``[name, reason]`` pairs.
    """
    return {
        "query": "test",
        "results": results if results is not None else [_default_result()],
        "answers": [],
        "corrections": [],
        "infoboxes": [],
        "suggestions": [],
        "unresponsive_engines": unresponsive or [],
    }


def _default_result() -> dict[str, Any]:
    return {
        "url": "https://example.com/article",
        "title": "An Example Result",
        "content": "A snippet of text describing the result.",
        "engine": "mojeek",
        "engines": ["mojeek", "qwant"],
        "score": 2.5,
        "category": "general",
        "template": "default.html",
        "publishedDate": None,
        "img_src": "",
        "thumbnail": "",
        "parsed_url": ["https", "example.com", "/article", "", "", ""],
        "positions": [1],
    }
