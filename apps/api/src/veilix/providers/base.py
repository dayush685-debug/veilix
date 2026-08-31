"""The search provider interface."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from veilix.domain.models import EngineInfo, SearchOutcome, SearchQuery


@runtime_checkable
class SearchProvider(Protocol):
    """Something that can answer searches.

    Implementations translate their own failure modes into the
    `veilix.core.errors` taxonomy. Callers handle `UpstreamTimeoutError`; they
    should never have to catch `httpx.ConnectError`.
    """

    @property
    def name(self) -> str:
        """Stable identifier, used as a metric label."""
        ...

    async def search(self, query: SearchQuery) -> SearchOutcome:
        """Run a search.

        Partial success is success: results from four engines while three were
        CAPTCHA-blocked returns normally, with the failures in
        `SearchOutcome.failures`.
        """
        ...

    async def suggest(self, text: str) -> tuple[str, ...]:
        """Autocomplete suggestions, or `()` if unavailable.

        Must not raise. A failing suggestion backend should not break the
        search box someone is typing into.
        """
        ...

    async def engines(self) -> tuple[EngineInfo, ...]:
        """Available engines and their capabilities."""
        ...

    async def healthy(self) -> bool:
        """Whether the backend is reachable. Must not raise."""
        ...
