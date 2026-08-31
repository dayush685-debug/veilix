"""Public API contract.

These models *are* the contract: FastAPI derives request validation, response
serialisation, and the OpenAPI document from them, so the documentation cannot
drift from the implementation the way a hand-maintained spec does.

They are kept separate from `domain.models` on purpose. The domain describes
what a search is; these describe what we promise over HTTP. Collapsing them
would mean every internal refactor became a breaking API change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from veilix.domain.models import (
    ResultKind,
    SafeSearch,
    SearchCategory,
    SearchOutcome,
    SearchResult,
    TimeRange,
)

# Length cap on the query. Long queries are not useful to any upstream engine
# and an uncapped string is a free amplification vector, the API would relay
# arbitrary bytes to dozens of engines on request.
MAX_QUERY_LENGTH = 512


class SearchParams(BaseModel):
    """Validated search request parameters.

    Every field is constrained. Unconstrained input reaching a fan-out to
    dozens of upstream engines is how a search API becomes someone else's
    denial-of-service tool.
    """

    model_config = ConfigDict(extra="forbid")

    q: Annotated[
        str,
        Field(
            min_length=1,
            max_length=MAX_QUERY_LENGTH,
            description="Search query.",
            examples=["distributed systems"],
        ),
    ]
    category: SearchCategory = Field(
        default=SearchCategory.GENERAL, description="Result category to search."
    )
    page: Annotated[int, Field(ge=1, le=10, description="1-indexed page number.")] = 1
    language: Annotated[
        str,
        Field(
            max_length=10,
            pattern=r"^(auto|[a-z]{2}(-[A-Z]{2})?)$",
            description="Language filter as 'auto', 'en', or 'en-US'.",
        ),
    ] = "auto"
    safesearch: SafeSearch = Field(
        default=SafeSearch.MODERATE, description="0 off, 1 moderate, 2 strict."
    )
    time_range: TimeRange | None = Field(default=None, description="Restrict results by recency.")
    engines: Annotated[
        str,
        Field(
            default="",
            max_length=200,
            pattern=r"^[a-zA-Z0-9 ._,-]*$",
            description="Comma-separated engine names. Empty uses the category default.",
        ),
    ] = ""

    def engine_tuple(self) -> tuple[str, ...]:
        return tuple(e.strip() for e in self.engines.split(",") if e.strip())


class MediaSchema(BaseModel):
    """Media attached to a result.

    ``image_url`` is always a Veilix proxy path, never a third-party URL. That
    is what keeps a rendered results page from leaking the viewer's IP to
    every image host in the result set (docs/privacy.md §6).
    """

    image_url: str | None = None
    thumbnail_url: str | None = None
    width: int | None = None
    height: int | None = None
    duration_s: int | None = None
    image_format: str | None = None


class SearchResultSchema(BaseModel):
    url: str
    title: str
    snippet: str
    kind: ResultKind
    domain: str = Field(description="Display host, for provenance in the interface.")
    engines: list[str] = Field(description="Upstream engines that returned this result.")
    score: float
    published_at: datetime | None = None
    author: str | None = None
    media: MediaSchema | None = None

    @classmethod
    def from_domain(cls, result: SearchResult) -> SearchResultSchema:
        return cls(
            url=result.url,
            title=result.title,
            snippet=result.snippet,
            kind=result.kind,
            domain=result.display_domain,
            engines=list(result.engines),
            score=result.score,
            published_at=result.published_at,
            author=result.author,
            media=(
                MediaSchema(
                    image_url=result.media.image_url,
                    thumbnail_url=result.media.thumbnail_url,
                    width=result.media.width,
                    height=result.media.height,
                    duration_s=result.media.duration_s,
                    image_format=result.media.image_format,
                )
                if result.media
                else None
            ),
        )


class EngineFailureSchema(BaseModel):
    """An upstream engine that did not answer this query."""

    engine: str
    reason: str


class InfoboxSchema(BaseModel):
    title: str
    content: str
    url: str | None = None
    image_url: str | None = None
    attributes: list[tuple[str, str]] = Field(default_factory=list)


class SearchTiming(BaseModel):
    """Where the time went.

    Returned to the client because a search that took two seconds because
    three engines were slow is a different experience from one that took two
    seconds because the server was busy, and the interface can say which.
    """

    total_ms: float
    upstream_ms: float | None = None
    cached: bool


class SearchResponse(BaseModel):
    """A search result set, including how well the search went.

    There is no ``total_results`` field. A live probe measured SearXNG
    returning ``number_of_results: null`` for a general query, so any total
    here would be fabricated. ``count`` is the number of results on this page,
    which is the number we actually know.
    """

    query: str
    category: SearchCategory
    page: int
    count: int = Field(description="Results on this page. Not a web-scale total.")
    results: list[SearchResultSchema]

    degraded: bool = Field(
        description=(
            "True when one or more upstream engines failed. The results are "
            "still usable; they come from fewer sources than usual."
        )
    )
    failures: list[EngineFailureSchema] = Field(
        default_factory=list,
        description="Engines that did not answer, with the reason each gave.",
    )
    engines_used: list[str] = Field(
        default_factory=list, description="Engines that contributed results."
    )

    answers: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    corrections: list[str] = Field(default_factory=list)
    infoboxes: list[InfoboxSchema] = Field(default_factory=list)

    timing: SearchTiming

    @classmethod
    def from_domain(cls, outcome: SearchOutcome, total_ms: float) -> SearchResponse:
        return cls(
            query=outcome.query.text,
            category=outcome.query.category,
            page=outcome.query.page,
            count=len(outcome.results),
            results=[SearchResultSchema.from_domain(r) for r in outcome.results],
            degraded=outcome.is_degraded,
            failures=[
                EngineFailureSchema(engine=f.engine, reason=f.reason) for f in outcome.failures
            ],
            engines_used=list(outcome.responding_engines),
            answers=list(outcome.answers),
            suggestions=list(outcome.suggestions),
            corrections=list(outcome.corrections),
            infoboxes=[
                InfoboxSchema(
                    title=b.title,
                    content=b.content,
                    url=b.url,
                    image_url=b.image_url,
                    attributes=[(label, value) for label, value in b.attributes],
                )
                for b in outcome.infoboxes
            ],
            timing=SearchTiming(
                total_ms=round(total_ms, 2),
                upstream_ms=outcome.upstream_ms,
                cached=outcome.from_cache,
            ),
        )


class SuggestionsResponse(BaseModel):
    query: str
    suggestions: list[str]


class EngineSchema(BaseModel):
    """Engine capabilities, read from live upstream configuration."""

    name: str
    categories: list[str]
    enabled: bool
    shortcut: str = ""
    supports_paging: bool = False
    supports_time_range: bool = False
    supports_safesearch: bool = False


class EnginesResponse(BaseModel):
    count: int
    enabled_count: int
    categories: list[str]
    engines: list[EngineSchema]


class ProblemDetail(BaseModel):
    """RFC 9457 problem details, the shape every error response takes."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "type": "https://veilix.dev/problems/rate-limited",
                "title": "Too Many Requests",
                "status": 429,
                "detail": "Request budget exhausted. Retry after the indicated interval.",
                "request_id": "9f2c1b7e4a6d",
                "retry_after": 42,
            }
        }
    )

    type: str
    title: str
    status: int
    detail: str
    request_id: str | None = None
