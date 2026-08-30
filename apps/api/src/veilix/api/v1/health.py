"""Health, readiness, liveness, and metrics.

Liveness and readiness are genuinely different questions and are answered by
different endpoints. Conflating them is a common and expensive mistake: if
readiness failures restart the process, then a brief Valkey outage becomes a
restart loop across every replica at once, turning a degraded dependency into
an outage.

- **/live** — "is this process running?" Never touches a dependency.
- **/ready** — "should traffic be routed here?" Checks dependencies.
- **/health** — human-facing detail for operators.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from veilix.api.deps import BreakerDep, SettingsDep
from veilix.core.telemetry import REGISTRY

router = APIRouter(tags=["operations"])


class LivenessResponse(BaseModel):
    status: Literal["alive"]


class ComponentHealth(BaseModel):
    name: str
    healthy: bool
    detail: str | None = None


class ReadinessResponse(BaseModel):
    ready: bool
    components: list[ComponentHealth]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    environment: str
    breaker_state: str
    components: list[ComponentHealth]


@router.get(
    "/live",
    response_model=LivenessResponse,
    summary="Liveness probe",
    description=(
        "Returns 200 whenever the process is running. Deliberately checks no "
        "dependency: a liveness probe that fails when Valkey is down would have "
        "the orchestrator kill a healthy process for someone else's outage."
    ),
)
async def live() -> LivenessResponse:
    return LivenessResponse(status="alive")


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description=(
        "Reports whether this instance should receive traffic. The search backend "
        "is required; the cache is not, because search degrades to slower rather "
        "than broken without it."
    ),
)
async def ready(request: Request, response: Response) -> ReadinessResponse:
    components = await _check_components(request)

    # Only genuinely required dependencies gate readiness. Valkey is reported
    # but does not remove the instance from rotation: losing the cache costs
    # latency, and pulling every replica out of rotation over it would cost
    # the whole service.
    required = {"searxng"}
    is_ready = all(c.healthy for c in components if c.name in required)

    if not is_ready:
        response.status_code = 503
    return ReadinessResponse(ready=is_ready, components=components)


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Operator-facing health detail",
)
async def health(request: Request, settings: SettingsDep, breaker: BreakerDep) -> HealthResponse:
    components = await _check_components(request)
    healthy = all(c.healthy for c in components)

    return HealthResponse(
        status="ok" if healthy else "degraded",
        version=request.app.version,
        environment=settings.env,
        breaker_state=breaker.state.value,
        components=components,
    )


@router.get(
    "/metrics",
    summary="Prometheus metrics",
    description=(
        "Aggregate operational metrics only. No label carries a user-derived "
        "value — no IP, no hashed IP, no query text, no session identifier — so "
        "this endpoint cannot be mined into per-user behaviour "
        "(docs/privacy.md §8)."
    ),
    response_class=Response,
    responses={200: {"content": {CONTENT_TYPE_LATEST: {}}}},
)
async def metrics(settings: SettingsDep) -> Response:
    if not settings.metrics_enabled:
        return Response(status_code=404)
    return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)


async def _check_components(request: Request) -> list[ComponentHealth]:
    components: list[ComponentHealth] = []

    provider: Any = request.app.state.provider
    searxng_ok = await provider.healthy()
    components.append(
        ComponentHealth(
            name="searxng",
            healthy=searxng_ok,
            detail=None if searxng_ok else "Search backend is not reachable.",
        )
    )

    redis: Any = request.app.state.redis
    try:
        await redis.ping()
        cache_ok, detail = True, None
    except Exception as exc:
        cache_ok = False
        # Type name only. An exception message from a connection failure can
        # contain the connection string, and that carries credentials.
        detail = f"Cache unreachable ({type(exc).__name__}); search still works, slower."
    components.append(ComponentHealth(name="valkey", healthy=cache_ok, detail=detail))

    return components
