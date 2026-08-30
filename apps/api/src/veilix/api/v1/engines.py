"""Engine catalogue.

Served from live upstream configuration rather than a hardcoded list. A static
list would drift the moment an operator edits `settings.yml` or upstream adds
an engine, and the interface would then offer filters that silently do nothing.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from veilix.domain.models import SearchCategory
from veilix.schemas.search import EngineSchema, EnginesResponse, ProblemDetail

router = APIRouter(tags=["search"])


@router.get(
    "/engines",
    response_model=EnginesResponse,
    responses={503: {"model": ProblemDetail, "description": "Backend unavailable."}},
    summary="List available search engines and their capabilities",
    description=(
        "Read from the live backend configuration, so the capability flags "
        "(`supports_paging`, `supports_time_range`, `supports_safesearch`) always "
        "reflect what the engines can actually do.\n\n"
        "`categories` lists only the categories Veilix exposes — a curated subset "
        "of the backend's full set, restricted to those that have engines "
        "configured and were verified to return results."
    ),
)
async def list_engines(
    request: Request,
    category: str | None = Query(
        default=None, max_length=32, description="Filter to one category."
    ),
    enabled_only: bool = Query(
        default=True, description="Exclude engines disabled in configuration."
    ),
) -> EnginesResponse:
    engines = await request.app.state.provider.engines()

    if enabled_only:
        engines = tuple(e for e in engines if e.enabled)
    if category:
        engines = tuple(e for e in engines if category in e.categories)

    return EnginesResponse(
        count=len(engines),
        enabled_count=sum(1 for e in engines if e.enabled),
        categories=[c.value for c in SearchCategory],
        engines=[
            EngineSchema(
                name=e.name,
                categories=list(e.categories),
                enabled=e.enabled,
                shortcut=e.shortcut,
                supports_paging=e.supports_paging,
                supports_time_range=e.supports_time_range,
                supports_safesearch=e.supports_safesearch,
            )
            for e in sorted(engines, key=lambda e: e.name)
        ],
    )
