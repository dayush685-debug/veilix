"""Search orchestration.

Composes the pieces in the order that matters:

    cache lookup -> circuit breaker -> provider -> metrics

The ordering is a design decision, not an accident. The cache is consulted
*before* the breaker so that a cached answer is still served while SearXNG is
down — the breaker exists to stop hammering a struggling dependency, and a
cache hit does not touch it at all. Reversing the two would turn a recoverable
upstream outage into a total one for queries we could already answer.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from veilix.core.errors import UpstreamError
from veilix.core.logging import get_logger
from veilix.core.telemetry import (
    engine_failures_total,
    engine_results_total,
    search_duration_seconds,
    search_requests_total,
    search_results_returned,
)
from veilix.domain.models import (
    EngineFailure,
    Infobox,
    Media,
    ResultKind,
    SearchOutcome,
    SearchQuery,
    SearchResult,
)
from veilix.infrastructure.cache import ResultCache
from veilix.infrastructure.circuit_breaker import CircuitBreaker
from veilix.providers.base import SearchProvider
from veilix.providers.searxng import classify_failure

log = get_logger(__name__)


class SearchService:
    """Answers searches, degrading rather than failing wherever possible."""

    def __init__(
        self,
        provider: SearchProvider,
        cache: ResultCache,
        breaker: CircuitBreaker,
    ) -> None:
        self._provider = provider
        self._cache = cache
        self._breaker = breaker

    async def search(self, query: SearchQuery) -> SearchOutcome:
        started = time.perf_counter()
        fingerprint = query.cache_fingerprint()

        cached = await self._cache.get(fingerprint)
        if cached is not None:
            outcome = _outcome_from_cache(query, cached)
            self._record(query, outcome, started, cache="hit")
            return outcome

        try:
            outcome = await self._breaker.call(lambda: self._provider.search(query))
        except UpstreamError:
            search_requests_total.labels(
                category=query.category.value, outcome="upstream_error"
            ).inc()
            raise

        # Store before recording metrics so a serialisation bug surfaces as a
        # cache write failure rather than as a skipped measurement.
        await self._cache.set(fingerprint, _outcome_to_cache(outcome))

        self._record(query, outcome, started, cache="miss")
        return outcome

    async def suggest(self, text: str) -> tuple[str, ...]:
        """Autocomplete. Deliberately bypasses cache and breaker.

        Suggestions are cheap, best-effort, and already fail soft in the
        provider. Routing them through the breaker would let a suggestion
        outage trip the circuit that protects *search*, which is the more
        important operation — a noisy neighbour problem created by sharing a
        breaker between a critical and a decorative call.
        """
        return await self._provider.suggest(text)

    # ------------------------------------------------------------------

    def _record(
        self, query: SearchQuery, outcome: SearchOutcome, started: float, *, cache: str
    ) -> None:
        elapsed = time.perf_counter() - started

        search_duration_seconds.labels(category=query.category.value, cache=cache).observe(elapsed)
        search_results_returned.observe(len(outcome.results))
        search_requests_total.labels(
            category=query.category.value,
            outcome="degraded" if outcome.is_degraded else "ok",
        ).inc()

        for engine in outcome.responding_engines:
            engine_results_total.labels(engine=engine).inc()

        # Real failure data from real queries, rather than a synthetic health
        # probe that would itself consume upstream quota (ADR-0006).
        for failure in outcome.failures:
            engine_failures_total.labels(
                engine=failure.engine, reason=classify_failure(failure.reason)
            ).inc()

        # Note what is absent: no query text, no client identity. This log line
        # says how the system behaved, not who asked what (docs/privacy.md §4).
        log.info(
            "search_completed",
            category=query.category.value,
            page=query.page,
            results=len(outcome.results),
            engines_down=len(outcome.failures),
            cache=cache,
            duration_ms=round(elapsed * 1000, 1),
        )


# ---------------------------------------------------------------------------
# Cache serialisation
#
# A hand-written mapping rather than pickle. Pickle would deserialise
# arbitrary objects from the cache, turning any write access to Valkey into
# remote code execution in the API process. The cache is shared, ephemeral,
# and reachable by anything on the backend network, which is precisely the
# situation where that matters.
# ---------------------------------------------------------------------------


def _outcome_to_cache(outcome: SearchOutcome) -> dict[str, Any]:
    return {
        "results": [
            {
                "url": r.url,
                "title": r.title,
                "snippet": r.snippet,
                "kind": r.kind.value,
                "engines": list(r.engines),
                "score": r.score,
                "published_at": r.published_at.isoformat() if r.published_at else None,
                "author": r.author,
                "metadata": r.metadata,
                "media": (
                    {
                        "image_url": r.media.image_url,
                        "thumbnail_url": r.media.thumbnail_url,
                        "width": r.media.width,
                        "height": r.media.height,
                        "duration_s": r.media.duration_s,
                        "image_format": r.media.image_format,
                    }
                    if r.media
                    else None
                ),
            }
            for r in outcome.results
        ],
        "failures": [{"engine": f.engine, "reason": f.reason} for f in outcome.failures],
        "suggestions": list(outcome.suggestions),
        "answers": list(outcome.answers),
        "corrections": list(outcome.corrections),
        "infoboxes": [
            {
                "title": b.title,
                "content": b.content,
                "url": b.url,
                "image_url": b.image_url,
                "attributes": [list(a) for a in b.attributes],
            }
            for b in outcome.infoboxes
        ],
        "upstream_ms": outcome.upstream_ms,
    }


def _outcome_from_cache(query: SearchQuery, payload: dict[str, Any]) -> SearchOutcome:
    return SearchOutcome(
        query=query,
        results=tuple(_result_from_cache(r) for r in payload.get("results", [])),
        failures=tuple(
            EngineFailure(engine=f["engine"], reason=f["reason"])
            for f in payload.get("failures", [])
        ),
        suggestions=tuple(payload.get("suggestions", [])),
        answers=tuple(payload.get("answers", [])),
        corrections=tuple(payload.get("corrections", [])),
        infoboxes=tuple(
            Infobox(
                title=b["title"],
                content=b["content"],
                url=b.get("url"),
                image_url=b.get("image_url"),
                attributes=tuple(tuple(a) for a in b.get("attributes", [])),
            )
            for b in payload.get("infoboxes", [])
        ),
        upstream_ms=payload.get("upstream_ms"),
        from_cache=True,
    )


def _result_from_cache(raw: dict[str, Any]) -> SearchResult:
    media_raw = raw.get("media")
    published = raw.get("published_at")
    return SearchResult(
        url=raw["url"],
        title=raw["title"],
        snippet=raw.get("snippet", ""),
        kind=ResultKind(raw.get("kind", ResultKind.WEB.value)),
        engines=tuple(raw.get("engines", [])),
        score=float(raw.get("score", 0.0)),
        published_at=datetime.fromisoformat(published) if published else None,
        author=raw.get("author"),
        media=Media(**media_raw) if media_raw else None,
        metadata=raw.get("metadata"),
    )
