"""Operations endpoints, behind HTTP Basic over TLS.

What this deliberately does not expose: individual searches, query text,
client addresses, per-user anything. The dashboard answers "is the system
healthy" and never "what did people search for". An admin panel that could
answer the second question would be a surveillance tool that happens to have a
login page, and building it would undo the product.

Every figure here is an aggregate already present in the Prometheus registry,
so the dashboard cannot show something the metrics endpoint does not.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from veilix.api.deps import BreakerDep, SettingsDep
from veilix.core.config import Settings
from veilix.core.security import require_admin_or_raise
from veilix.core.telemetry import REGISTRY
from veilix.schemas.search import ProblemDetail

# auto_error=False so a missing credential reaches our own handler and returns
# problem+json like every other error, instead of Starlette's bare 403.
_basic = HTTPBasic(auto_error=False, description="Administrator credentials.")

router = APIRouter(
    tags=["admin"],
    responses={401: {"model": ProblemDetail, "description": "Authentication required."}},
)


async def require_admin(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(_basic)] = None,
) -> None:
    settings: Settings = request.app.state.settings
    require_admin_or_raise(
        credentials.username if credentials else "",
        credentials.password if credentials else "",
        expected_username=settings.admin_username,
        password_hash=settings.admin_password_hash,
    )


class EngineHealthEntry(BaseModel):
    """Health of one upstream engine.

    Derived from failures observed on real queries, which is why an engine
    nobody has searched recently has no entry here at all, its health is
    *unknown*, not good. Showing a green tick for an engine we have not
    exercised would be a claim we have not earned.
    """

    engine: str
    failures: int
    reasons: dict[str, int] = Field(default_factory=dict)


class OverviewResponse(BaseModel):
    environment: str
    version: str
    breaker_state: str

    requests_total: float
    searches_total: float
    searches_degraded: float

    cache_hits: float
    cache_misses: float
    cache_hit_ratio: float | None = Field(
        description="Null until at least one cache lookup has happened."
    )

    ratelimit_blocked: float
    engine_failures: list[EngineHealthEntry]
    engines_contributing: dict[str, float]

    cache_enabled: bool
    ratelimit_enabled: bool
    ratelimit_requests_per_window: int
    ratelimit_window_s: int


@router.get(
    "/admin/overview",
    response_model=OverviewResponse,
    dependencies=[Depends(require_admin)],
    summary="Aggregate operational overview",
    description=(
        "Privacy-safe operational aggregates. Contains no query text, no client "
        "identifiers, and no per-user data of any kind."
    ),
)
async def overview(
    request: Request, settings: SettingsDep, breaker: BreakerDep
) -> OverviewResponse:
    samples = _collect_samples()

    hits = samples.get(("veilix_cache_operations_total", ("hit",)), 0.0)
    misses = samples.get(("veilix_cache_operations_total", ("miss",)), 0.0)
    lookups = hits + misses

    return OverviewResponse(
        environment=settings.env,
        version=request.app.version,
        breaker_state=breaker.state.value,
        requests_total=_sum_metric(samples, "veilix_http_requests_total"),
        searches_total=_sum_metric(samples, "veilix_search_requests_total"),
        searches_degraded=samples.get(
            ("veilix_search_requests_total", ("general", "degraded")), 0.0
        ),
        cache_hits=hits,
        cache_misses=misses,
        # None rather than 0.0 when nothing has been measured. A hit ratio of
        # zero and "no data yet" mean different things, and a dashboard that
        # renders them identically will have someone debugging a healthy cache.
        cache_hit_ratio=round(hits / lookups, 4) if lookups else None,
        ratelimit_blocked=_sum_where(
            samples, "veilix_ratelimit_events_total", lambda lv: "blocked" in lv
        ),
        engine_failures=_engine_failures(samples),
        engines_contributing={
            labels[0]: value
            for (name, labels), value in samples.items()
            if name == "veilix_engine_results_total" and labels
        },
        cache_enabled=settings.cache_enabled,
        ratelimit_enabled=settings.ratelimit_enabled,
        ratelimit_requests_per_window=settings.ratelimit_requests,
        ratelimit_window_s=settings.ratelimit_window_s,
    )


# ---------------------------------------------------------------------------
# Metric readback
#
# The dashboard reads the same registry Prometheus scrapes instead of keeping
# its own counters. One source of truth means the panel and the alert can never
# disagree about what happened.
# ---------------------------------------------------------------------------

SampleKey = tuple[str, tuple[str, ...]]


def _collect_samples() -> dict[SampleKey, float]:
    collected: dict[SampleKey, float] = {}
    for metric in REGISTRY.collect():
        for sample in metric.samples:
            if sample.name.endswith(("_created", "_bucket")):
                continue
            name = sample.name.removesuffix("_total").removesuffix("_sum")
            base = f"{name}_total" if metric.type == "counter" else sample.name
            collected[(base, tuple(sample.labels.values()))] = sample.value
    return collected


def _sum_metric(samples: dict[SampleKey, float], name: str) -> float:
    return sum(v for (n, _), v in samples.items() if n == name)


def _sum_where(samples: dict[SampleKey, float], name: str, predicate: Any) -> float:
    return sum(v for (n, lv), v in samples.items() if n == name and predicate(lv))


def _engine_failures(samples: dict[SampleKey, float]) -> list[EngineHealthEntry]:
    by_engine: dict[str, dict[str, int]] = {}
    for (name, labels), value in samples.items():
        if name != "veilix_engine_failures_total" or len(labels) < 2:
            continue
        engine, reason = labels[0], labels[1]
        by_engine.setdefault(engine, {})[reason] = int(value)

    entries = [
        EngineHealthEntry(engine=e, failures=sum(r.values()), reasons=r)
        for e, r in by_engine.items()
    ]
    return sorted(entries, key=lambda e: e.failures, reverse=True)
