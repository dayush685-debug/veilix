"""Domain entities.

This module imports nothing from FastAPI, httpx, redis, or any provider. It
describes what a search *is* in Veilix terms, independent of the fact that
SearXNG happens to answer it today.

The concrete payoff: when the SearXNG JSON shape changes — and it does, the
project renamed its datastore key between releases — the damage is contained
to `providers/searxng.py`, which is the one module allowed to know that shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, StrEnum


class SearchCategory(StrEnum):
    """Categories Veilix exposes.

    A deliberate subset of SearXNG's 32. Each one here has engines configured,
    was verified to return results, and has a sensible presentation in the UI.
    Exposing all 32 would advertise categories that return nothing.
    """

    GENERAL = "general"
    NEWS = "news"
    IMAGES = "images"
    VIDEOS = "videos"
    IT = "it"
    SCIENCE = "science"
    FILES = "files"
    MAP = "map"
    MUSIC = "music"
    SOCIAL = "social media"


class TimeRange(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


class SafeSearch(int, Enum):
    OFF = 0
    MODERATE = 1
    STRICT = 2


class ResultKind(StrEnum):
    """What a result *is*, which decides how it renders.

    Derived from the upstream template name rather than the category, because
    an image can appear in a general search and should still render as an
    image.
    """

    WEB = "web"
    IMAGE = "image"
    VIDEO = "video"
    TORRENT = "torrent"
    MAP = "map"
    PAPER = "paper"
    PACKAGE = "package"


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """A normalised search request.

    Normalisation happens once, here, and the same object is used both to
    build the upstream call and to derive the cache key — so a cache hit is
    guaranteed to correspond to an identical upstream request. Deriving them
    separately is how caches quietly start returning the wrong results.
    """

    text: str
    category: SearchCategory = SearchCategory.GENERAL
    page: int = 1
    language: str = "auto"
    safesearch: SafeSearch = SafeSearch.MODERATE
    time_range: TimeRange | None = None
    engines: tuple[str, ...] = ()

    def cache_fingerprint(self) -> str:
        """Stable string identifying this query for caching.

        Contains only query parameters. There is no identity component, which
        is exactly what makes the cache shareable between users and therefore
        privacy-compatible — and also what creates the timing side channel
        documented in docs/privacy.md §7.
        """
        parts = [
            self.text.strip().casefold(),
            self.category.value,
            str(self.page),
            self.language,
            str(self.safesearch.value),
            self.time_range.value if self.time_range else "",
            ",".join(sorted(self.engines)),
        ]
        return "|".join(parts)


@dataclass(frozen=True, slots=True)
class Media:
    """Media attached to a result.

    ``image_url`` is populated only after proxy rewriting. The raw upstream URL
    never reaches this object, so it cannot leak into a response by omission.
    """

    image_url: str | None = None
    thumbnail_url: str | None = None
    width: int | None = None
    height: int | None = None
    duration_s: int | None = None
    image_format: str | None = None


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One result, provider-agnostic.

    ``engines`` is a tuple because a result may be reported by several upstream
    engines; agreement between them is a relevance signal and is shown in the
    interface as provenance.
    """

    url: str
    title: str
    snippet: str
    kind: ResultKind
    engines: tuple[str, ...]
    score: float = 0.0
    published_at: datetime | None = None
    author: str | None = None
    media: Media | None = None
    # Provider-specific extras that have no domain meaning, carried through for
    # display without being promoted to first-class fields.
    metadata: str | None = None

    @property
    def display_domain(self) -> str:
        """Registrable-ish domain for display, without importing a URL parser.

        Best-effort and presentation-only; nothing security-relevant depends
        on this value.
        """
        remainder = self.url.split("://", 1)[-1]
        host = remainder.split("/", 1)[0].split("@")[-1].split(":")[0]
        return host[4:] if host.startswith("www.") else host


@dataclass(frozen=True, slots=True)
class EngineFailure:
    """An upstream engine that did not answer.

    Surfaced to the client rather than hidden. A search returning results from
    four engines while three were CAPTCHA-blocked is a materially different
    answer from one where all seven responded, and the user is entitled to
    know which they received.
    """

    engine: str
    reason: str


@dataclass(frozen=True, slots=True)
class Infobox:
    title: str
    content: str
    url: str | None = None
    image_url: str | None = None
    attributes: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    """The complete result of a search, including how well it went.

    There is no ``total_results`` field, and its absence is deliberate. A live
    probe showed SearXNG returning ``number_of_results: null`` for a general
    query, so any total shown here would be invented. ``len(results)`` is what
    we actually know.
    """

    query: SearchQuery
    results: tuple[SearchResult, ...]
    failures: tuple[EngineFailure, ...] = ()
    suggestions: tuple[str, ...] = ()
    answers: tuple[str, ...] = ()
    corrections: tuple[str, ...] = ()
    infoboxes: tuple[Infobox, ...] = ()
    # Upstream call duration in milliseconds; None when served from cache.
    upstream_ms: float | None = None
    from_cache: bool = False

    @property
    def is_degraded(self) -> bool:
        return bool(self.failures)

    @property
    def responding_engines(self) -> tuple[str, ...]:
        seen: set[str] = set()
        for result in self.results:
            seen.update(result.engines)
        return tuple(sorted(seen))


@dataclass(frozen=True, slots=True)
class EngineInfo:
    """Capabilities of one upstream engine, as reported by the provider.

    Read from live provider configuration rather than hardcoded, so the
    interface cannot advertise a filter an engine does not support, and cannot
    rot when upstream changes an engine's capabilities.
    """

    name: str
    categories: tuple[str, ...]
    enabled: bool
    shortcut: str = ""
    supports_paging: bool = False
    supports_time_range: bool = False
    supports_safesearch: bool = False
    timeout_s: float = 0.0


@dataclass
class ProviderHealth:
    """Observed health of the search backend.

    Populated from real query outcomes rather than synthetic probes: engine
    health comes from `unresponsive_engines` on responses we already made
    (ADR-0006). Engines nobody has queried recently are *unknown*, not healthy,
    and the dashboard must say so rather than showing an unearned green tick.
    """

    reachable: bool
    breaker_state: str
    recent_failures: dict[str, str] = field(default_factory=dict)
    engines_seen_ok: set[str] = field(default_factory=set)
