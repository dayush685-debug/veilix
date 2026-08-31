"""Search endpoints."""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Query

from veilix.api.deps import SearchServiceDep
from veilix.domain.models import SearchQuery
from veilix.schemas.search import (
    ProblemDetail,
    SearchParams,
    SearchResponse,
    SuggestionsResponse,
)

router = APIRouter(tags=["search"])

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ProblemDetail, "description": "Invalid parameters."},
    429: {"model": ProblemDetail, "description": "Rate limit exceeded."},
    503: {"model": ProblemDetail, "description": "Search backend unavailable."},
    504: {"model": ProblemDetail, "description": "Search backend timed out."},
}


@router.get(
    "/search",
    response_model=SearchResponse,
    responses=_ERROR_RESPONSES,
    summary="Search the web through aggregated engines",
    description=(
        "Runs a query across multiple upstream search engines and returns merged "
        "results.\n\n"
        "**Partial results are a success.** Upstream engines frequently CAPTCHA or "
        "rate-limit self-hosted instances, so a normal response may report engines "
        "in `failures` while still returning useful results. Check `degraded` "
        "rather than assuming an empty `failures` list.\n\n"
        "`count` is the number of results on this page. It is deliberately not a "
        "web-scale total, because the upstream does not report one."
    ),
)
async def search(
    service: SearchServiceDep,
    # Query() instead of Depends(): with Depends, FastAPI extracts each field
    # individually and the model's `extra="forbid"` is never applied, so a
    # typo like `safe_search=2` would be ignored without complaint and the caller would
    # get moderate filtering while believing they asked for strict. As a query
    # parameter model, unknown parameters are rejected with a 422 that names
    # them.
    params: Annotated[SearchParams, Query()],
) -> SearchResponse:
    started = time.perf_counter()

    outcome = await service.search(
        SearchQuery(
            text=params.q,
            category=params.category,
            page=params.page,
            language=params.language,
            safesearch=params.safesearch,
            time_range=params.time_range,
            engines=params.engine_tuple(),
        )
    )

    return SearchResponse.from_domain(outcome, (time.perf_counter() - started) * 1000)


@router.get(
    "/search/suggestions",
    response_model=SuggestionsResponse,
    summary="Autocomplete suggestions for a partial query",
    description=(
        "Suggestions are fetched server-side, so the user's browser never contacts "
        "the suggestion provider and that provider never sees the user's IP.\n\n"
        "Returns an empty list rather than an error when suggestions are "
        "unavailable: a failing autocomplete must not break the search box."
    ),
)
async def suggestions(
    service: SearchServiceDep,
    q: Annotated[
        str,
        Query(min_length=1, max_length=100, description="Partial query text."),
    ],
) -> SuggestionsResponse:
    return SuggestionsResponse(query=q, suggestions=list(await service.suggest(q)))
