"""The search provider seam.

One Protocol, one implementation today. The seam exists because it is the
difference between "a FastAPI wrapper around SearXNG" and "a platform that
currently uses SearXNG", and because it is what the AI-readiness requirement
actually amounts to: a future semantic-search or summarisation stage
implements this interface and the API, services, and domain layers do not
change.

A ``Protocol`` rather than an abstract base class, so implementations are
structurally typed. Nothing has to import Veilix to satisfy it, which keeps
test doubles free of inheritance ceremony.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from veilix.domain.models import EngineInfo, SearchOutcome, SearchQuery


@runtime_checkable
class SearchProvider(Protocol):
    """Something that can answer searches.

    Implementations are responsible for translating their own failure modes
    into the ``veilix.core.errors`` taxonomy. Callers handle
    ``UpstreamTimeoutError`` and ``UpstreamUnavailableError``; they must never
    have to catch ``httpx.ConnectError`` — that would leak the transport into
    every layer above and defeat the point of the seam.
    """

    @property
    def name(self) -> str:
        """Stable identifier, used as a bounded metric label."""
        ...

    async def search(self, query: SearchQuery) -> SearchOutcome:
        """Execute a search.

        Partial success is success. A result set from four engines while three
        were CAPTCHA-blocked returns normally, with the failures recorded in
        ``SearchOutcome.failures`` — because on a self-hosted instance that is
        the ordinary case, not an error (ADR-0006).
        """
        ...

    async def suggest(self, text: str) -> tuple[str, ...]:
        """Autocomplete suggestions.

        Server-side so the user's browser never contacts the suggestion
        provider directly. Returns an empty tuple rather than raising when
        suggestions are unavailable: an autocomplete failure must never break
        the search box a person is typing into.
        """
        ...

    async def engines(self) -> tuple[EngineInfo, ...]:
        """Available engines and their capabilities, read from live config."""
        ...

    async def healthy(self) -> bool:
        """Whether the backend is reachable. Must not raise."""
        ...
