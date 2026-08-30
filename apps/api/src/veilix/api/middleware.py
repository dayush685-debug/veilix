"""Request-scoped middleware: correlation, metrics, and rate limiting.

Ordering matters and is set in ``main.py``. Correlation runs outermost so that
every log line — including one written by the rate limiter rejecting a
request — carries a request ID.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from veilix.core.errors import RateLimitedError, veilix_error_handler
from veilix.core.logging import get_logger, request_id_ctx
from veilix.core.security import client_ip_from_headers
from veilix.core.telemetry import (
    http_request_duration_seconds,
    http_requests_total,
    status_class,
)

log = get_logger(__name__)

RequestHandler = Callable[[Request], Awaitable[Response]]

# Header caps. An 8 KB header is not a legitimate request ID or forwarding
# chain; accepting one only gives an attacker somewhere free to put bytes.
_MAX_REQUEST_ID_LENGTH = 64
_MAX_FORWARDED_FOR_LENGTH = 256


def _route_template(request: Request) -> str:
    """The route pattern, never the resolved path.

    This is the difference between a metric label of ``/api/v1/search`` and one
    of ``/api/v1/search?q=<someone's query>``. The first is operational
    telemetry; the second is a permanent record of what people searched for,
    stored in a system nobody thinks of as a database (docs/privacy.md §8).
    """
    route = request.scope.get("route")
    path: str | None = getattr(route, "path", None)
    return path or "unmatched"


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Assigns a request ID and binds it for the duration of the request.

    The ID correlates log lines *within one request*. It is regenerated per
    request and is not a session identifier — two requests from the same
    person share nothing, which is what stops it becoming a tracking token by
    accident.
    """

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        inbound = (request.headers.get("X-Request-ID") or "")[:_MAX_REQUEST_ID_LENGTH]
        # Accept an upstream ID only if it looks like one. Echoing arbitrary
        # client bytes into every log line invites log injection.
        request_id = inbound if inbound.isalnum() and len(inbound) >= 8 else secrets.token_hex(8)

        request.state.request_id = request_id
        token = request_id_ctx.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token)

        response.headers["X-Request-ID"] = request_id
        return response


class MetricsMiddleware(BaseHTTPMiddleware):
    """Records request counts and latency with bounded label cardinality."""

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Count the failure before re-raising, so a crashing endpoint is
            # visible in metrics rather than only in logs.
            http_requests_total.labels(
                method=request.method, route=_route_template(request), status_class="5xx"
            ).inc()
            raise

        elapsed = time.perf_counter() - started
        route = _route_template(request)

        http_requests_total.labels(
            method=request.method,
            route=route,
            status_class=status_class(response.status_code),
        ).inc()
        http_request_duration_seconds.labels(method=request.method, route=route).observe(elapsed)

        response.headers["X-Response-Time-Ms"] = f"{elapsed * 1000:.1f}"
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforces per-identity limits before a request reaches a route.

    Implemented as middleware rather than a route dependency so that it cannot
    be forgotten on a new endpoint. A limiter you have to remember to apply is
    a limiter that will eventually be missing from the one route that needed
    it most.
    """

    # Liveness and readiness must answer while the limiter is shedding load,
    # or an orchestrator will read 429 as "unhealthy" and restart a service
    # that is working exactly as designed under load.
    EXEMPT_PATHS = frozenset({"/api/v1/health", "/api/v1/live", "/api/v1/ready", "/api/v1/metrics"})

    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        limiter = request.app.state.rate_limiter
        settings = request.app.state.settings

        client_ip = client_ip_from_headers(
            forwarded_for=(request.headers.get("X-Forwarded-For") or "")[
                :_MAX_FORWARDED_FOR_LENGTH
            ],
            peer_ip=request.client.host if request.client else None,
            # X-Forwarded-For is honoured only where a trusted proxy sets it.
            # In development there is no proxy, so the header is ignored and
            # cannot be used to forge a fresh rate-limit bucket per request.
            trust_proxy=settings.is_production,
        )

        api_key = request.headers.get("X-API-Key")
        verified_key = (
            api_key if api_key and api_key_is_valid(api_key, settings.api_key_digests) else None
        )

        identity = limiter.identify(client_ip=client_ip, api_key=verified_key)
        request.state.identity = identity

        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)

        decision = await limiter.check(identity)
        if not decision.allowed:
            log.info(
                "rate_limited",
                identity_kind=identity.kind.value,
                limit=decision.limit,
                retry_after_s=decision.retry_after_s,
            )
            return await veilix_error_handler(
                request,
                RateLimitedError(retry_after=decision.retry_after_s, limit=decision.limit),
            )

        response = await call_next(request)

        # Standard advisory headers so clients can self-throttle instead of
        # discovering the limit by hitting it.
        response.headers["X-RateLimit-Limit"] = str(decision.limit)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        return response


def api_key_is_valid(key: str, digests: frozenset[str]) -> bool:
    """Thin wrapper kept here so middleware does not import security directly."""
    from veilix.core.security import verify_api_key

    return verify_api_key(key, digests)
