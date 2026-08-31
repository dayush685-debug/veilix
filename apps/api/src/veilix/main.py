"""Application factory and lifespan.

This module wires things together and holds no business logic. Everything it
constructs is long-lived and expensive to create, connection pools, the
provider, the breaker, so it is built once at startup and read back through
`api/deps.py`.

Startup is deliberately noisy about its own configuration. An operator should
be able to read the first few log lines and know whether caching is on, whether
the limiter is enforcing, and whether tracing is exporting, rather than
inferring it from behaviour later.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from redis.asyncio import Redis
from starlette.middleware.cors import CORSMiddleware

from veilix import __version__
from veilix.api.middleware import (
    CorrelationMiddleware,
    MetricsMiddleware,
    RateLimitMiddleware,
)
from veilix.api.v1.router import api_router
from veilix.core.config import Settings, get_settings
from veilix.core.errors import (
    VeilixError,
    unhandled_error_handler,
    validation_error_handler,
    veilix_error_handler,
)
from veilix.core.logging import configure_logging, get_logger
from veilix.core.telemetry import build_info, setup_tracing
from veilix.infrastructure.cache import NullResultCache, ValkeyResultCache
from veilix.infrastructure.circuit_breaker import CircuitBreaker
from veilix.infrastructure.ratelimit import RateLimiter
from veilix.providers.searxng import SearxngProvider
from veilix.services.search_service import SearchService

log = get_logger(__name__)

DESCRIPTION = """
A privacy-first meta-search API.

Queries are forwarded to many upstream search engines and merged. There are no
accounts, no cookies, no search history, and no user profiling, see the
privacy model for the complete data inventory, including what the operator of
an instance can still technically observe.

Two things to know when integrating:

*Partial results are normal.* Upstream engines routinely CAPTCHA or rate-limit
self-hosted instances. A successful response may list engines in `failures`
while still returning good results. Check the `degraded` flag instead of
assuming every engine answered.

*There is no total result count.* The upstream does not report one, so Veilix
does not invent one. `count` is the number of results on the current page.
"""


def _build_http_client(settings: Settings) -> httpx.AsyncClient:
    """One pooled client for the process.

    Connection reuse matters here: a search is a chain of calls to the same
    host, and a fresh TCP and TLS handshake per request would add latency to
    every one of them. The pool is sized above the expected concurrency so a
    burst queues on the upstream rather than on connection setup.
    """
    return httpx.AsyncClient(
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        timeout=httpx.Timeout(settings.search_timeout_s, connect=3.0),
        # Redirects are not followed: the only host we call is a fixed internal
        # address, so a redirect means something unexpected, and following it
        # would be the first step of an SSRF chain instead of a convenience.
        follow_redirects=False,
        headers={"User-Agent": f"Veilix/{__version__}"},
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings

    client = _build_http_client(settings)
    redis = Redis.from_url(
        settings.redis_url,
        decode_responses=False,
        socket_connect_timeout=2.0,
        socket_timeout=2.0,
        health_check_interval=30,
    )

    provider = SearxngProvider(
        client,
        base_url=str(settings.searxng_url),
        secret=settings.searxng_secret,
        timeout_s=settings.search_timeout_s,
        max_retries=settings.search_max_retries,
    )
    breaker = CircuitBreaker(
        name="searxng",
        fail_threshold=settings.breaker_fail_threshold,
        reset_timeout_s=settings.breaker_reset_timeout_s,
    )
    cache = (
        ValkeyResultCache(redis, ttl_s=settings.cache_ttl_s)
        if settings.cache_enabled
        else NullResultCache()
    )

    app.state.http_client = client
    app.state.redis = redis
    app.state.provider = provider
    app.state.breaker = breaker
    app.state.search_service = SearchService(provider, cache, breaker)
    app.state.rate_limiter = RateLimiter(
        redis,
        salt_seed=settings.effective_salt_seed(),
        anonymous_limit=settings.ratelimit_requests,
        api_key_limit=settings.ratelimit_apikey_requests,
        window_s=settings.ratelimit_window_s,
        enabled=settings.ratelimit_enabled,
    )

    build_info.labels(version=__version__, environment=settings.env).set(1)

    log.info(
        "startup",
        version=__version__,
        environment=settings.env,
        cache_enabled=settings.cache_enabled,
        cache_ttl_s=settings.cache_ttl_s,
        ratelimit_enabled=settings.ratelimit_enabled,
        ratelimit=f"{settings.ratelimit_requests}/{settings.ratelimit_window_s}s",
        api_keys_configured=len(settings.api_key_digests),
        admin_configured=bool(settings.admin_password_hash),
        tracing_enabled=getattr(app.state, "tracing_enabled", False),
        # Warn rather than fail: without the shared secret, image proxying
        # cannot be signed, so thumbnails would have to be dropped instead of
        # silently served from third-party hosts.
        image_proxy_signing=bool(settings.searxng_secret),
    )
    if not settings.searxng_secret:
        log.warning(
            "image_proxy_signing_disabled",
            impact="image results will omit thumbnails rather than leak user IPs",
            fix="set SEARXNG_SECRET to the same value the searxng container uses",
        )

    try:
        yield
    finally:
        await client.aclose()
        await redis.aclose()
        log.info("shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    configure_logging(settings.log_level, json_output=settings.is_production)

    app = FastAPI(
        title="Veilix API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        # Interactive docs are useful and are also a map of the attack surface.
        # Public instances serve the OpenAPI document but not the try-it-out UI.
        docs_url=None if settings.is_production else "/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        contact={"name": "Veilix", "url": "https://github.com/"},
        license_info={"name": "See repository"},
    )
    app.state.settings = settings

    app.add_exception_handler(VeilixError, veilix_error_handler)
    # One error shape for the whole API: FastAPI's default 422 body would
    # otherwise be the single response a client cannot parse like the rest.
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    # Starlette applies middleware in reverse registration order, so the last
    # registered runs outermost. Correlation must be outermost: a request
    # rejected by the limiter still needs a request ID in its log line and its
    # response.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(CorrelationMiddleware)

    if settings.cors_origin_list:
        # The SPA is same-origin behind Caddy and needs no CORS entry. This
        # exists for third-party API consumers, and is configured with explicit
        # origins, never a wildcard, which production config validation
        # rejects outright.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=False,
            allow_methods=["GET"],
            allow_headers=["X-API-Key", "X-Request-ID"],
            expose_headers=["X-Request-ID", "X-RateLimit-Remaining"],
            max_age=600,
        )

    app.include_router(api_router)

    # Tracing is set up HERE, not in lifespan.
    #
    # FastAPIInstrumentor.instrument_app adds middleware, and Starlette builds
    # its middleware stack once, on the first request. Lifespan runs after the
    # application object is assembled, so instrumenting there is too late for
    # the server middleware to be included: outgoing httpx spans still appear
    # and HTTP server spans silently do not.
    #
    # Measured, which is the only reason this was noticed - a traced search
    # produced exactly one span, from the httpx client, and no request span at
    # all. Tracing looked enabled and was half missing.
    app.state.tracing_enabled = setup_tracing(app, endpoint=settings.otlp_endpoint)

    return app


app = create_app()
