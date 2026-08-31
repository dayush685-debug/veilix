"""Dependency wiring.

The HTTP client, Valkey pool, provider and breaker are built once at startup
and stored on ``app.state``; these functions read them back. Building them per
request would give each request its own circuit breaker, which cannot count
consecutive failures and so is no breaker at all.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request

from veilix.core.config import Settings
from veilix.core.errors import AuthenticationRequiredError
from veilix.core.security import ClientIdentity, verify_api_key
from veilix.infrastructure.circuit_breaker import CircuitBreaker
from veilix.infrastructure.ratelimit import RateLimiter
from veilix.services.search_service import SearchService


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def get_search_service(request: Request) -> SearchService:
    return request.app.state.search_service  # type: ignore[no-any-return]


def get_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter  # type: ignore[no-any-return]


def get_breaker(request: Request) -> CircuitBreaker:
    return request.app.state.breaker  # type: ignore[no-any-return]


def get_provider(request: Request):  # type: ignore[no-untyped-def]
    return request.app.state.provider


def get_identity(request: Request) -> ClientIdentity:
    """The identity the rate-limiting middleware already established.

    Computed once in middleware and read here, so a route cannot accidentally
    derive a *different* identity from the one that was rate-limited.
    """
    return request.state.identity  # type: ignore[no-any-return]


async def require_api_key(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    """Guard for endpoints that require an API key.

    When no keys are configured the endpoint is refused rather than opened.
    An unconfigured deployment should fail closed: "we forgot to set this up"
    must not read as "everyone is welcome".
    """
    settings: Settings = request.app.state.settings
    digests = settings.api_key_digests

    if not digests:
        raise AuthenticationRequiredError(
            "API key authentication is not configured on this instance."
        )
    if not x_api_key or not verify_api_key(x_api_key, digests):
        raise AuthenticationRequiredError("A valid X-API-Key header is required.")


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
SearchServiceDep = Annotated[SearchService, Depends(get_search_service)]
RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]
BreakerDep = Annotated[CircuitBreaker, Depends(get_breaker)]
IdentityDep = Annotated[ClientIdentity, Depends(get_identity)]
