"""SearXNG provider adapter.

The single module that knows SearXNG's JSON shape. Everything above it works
in domain terms, so when upstream changes its response format the blast radius
is this file. That containment is not theoretical: between releases upstream
moved from uWSGI to Granian and renamed its datastore key from ``redis`` to
``valkey``, so the surface does move.

Response fields were derived by probing a live instance
(``searxng/searxng:2026.8.29-d226b78bc``), not from documentation.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime
from typing import Any, Final
from urllib.parse import urlencode

import httpx

from veilix.core.errors import (
    UpstreamError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from veilix.core.logging import get_logger
from veilix.core.urls import is_safe_to_proxy, is_safe_web_url
from veilix.domain.models import (
    EngineFailure,
    EngineInfo,
    Infobox,
    Media,
    ResultKind,
    SearchOutcome,
    SearchQuery,
    SearchResult,
)

log = get_logger(__name__)

# Public path the browser uses for proxied images. Caddy maps this to
# SearXNG's /image_proxy; the API never serves image bytes itself, because
# ADR-0004 leaves it without the internet access that would require.
IMAGE_PROXY_PATH: Final = "/img"

# Upstream template name to domain result kind. Upstream decides rendering by
# template rather than category, which is the more accurate signal — an image
# can appear in a general search and should still render as an image.
_TEMPLATE_KINDS: Final[dict[str, ResultKind]] = {
    "default.html": ResultKind.WEB,
    "images.html": ResultKind.IMAGE,
    "videos.html": ResultKind.VIDEO,
    "torrent.html": ResultKind.TORRENT,
    "map.html": ResultKind.MAP,
    "paper.html": ResultKind.PAPER,
    "packages.html": ResultKind.PACKAGE,
    "files.html": ResultKind.WEB,
    "key-value.html": ResultKind.WEB,
}

# Upstream reports failures as free text ("Suspended: too many requests").
# Free text is unusable as a metric label — it is unbounded cardinality — so
# it is folded into a small fixed set of causes.
_FAILURE_PATTERNS: Final[tuple[tuple[str, str], ...]] = (
    ("captcha", "captcha"),
    ("too many requests", "rate_limited"),
    ("access denied", "access_denied"),
    ("timeout", "timeout"),
    ("suspended", "suspended"),
    ("connection", "connection_error"),
)


def classify_failure(reason: str) -> str:
    """Fold an upstream failure string into a bounded label."""
    lowered = reason.casefold()
    for needle, label in _FAILURE_PATTERNS:
        if needle in lowered:
            return label
    return "other"


class SearxngProvider:
    """Talks to a SearXNG instance over its JSON API."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str,
        secret: str,
        timeout_s: float = 8.0,
        max_retries: int = 1,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._secret = secret
        self._timeout_s = timeout_s
        self._max_retries = max_retries

    @property
    def name(self) -> str:
        return "searxng"

    # ------------------------------------------------------------------
    # Image proxy signing
    # ------------------------------------------------------------------

    def _sign_image_url(self, url: str) -> str | None:
        """Rewrite a third-party image URL into a signed proxy URL.

        Without this, the JSON API hands the browser raw third-party URLs and
        every thumbnail leaks the user's IP to hosts they never chose. Setting
        ``image_proxy: true`` upstream is not enough on its own — it rewrites
        only SearXNG's own HTML rendering, and a live probe measured 0 of 264
        JSON image results as proxied.

        Upstream signs proxy URLs as ``HMAC-SHA256(secret_key, url)`` and we
        hold the same secret, so we can produce the same signature.

        Returns ``None`` when the URL must not be proxied. That capability is
        also what makes this a signing oracle for URLs chosen by whoever ranks
        in results, so ``is_safe_to_proxy`` gates it (SF-003).
        """
        if not url or not self._secret:
            return None
        if url.startswith("data:image/"):
            # Already inline; nothing is fetched, so nothing leaks.
            return url
        if not is_safe_to_proxy(url):
            return None

        signature = hmac.new(self._secret.encode(), url.encode(), hashlib.sha256).hexdigest()
        return f"{IMAGE_PROXY_PATH}?{urlencode({'url': url, 'h': signature})}"

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _build_params(self, query: SearchQuery) -> dict[str, str]:
        params: dict[str, str] = {
            "q": query.text,
            "format": "json",
            "categories": query.category.value,
            "pageno": str(query.page),
            "safesearch": str(query.safesearch.value),
        }
        if query.language and query.language != "auto":
            params["language"] = query.language
        if query.time_range:
            params["time_range"] = query.time_range.value
        if query.engines:
            params["engines"] = ",".join(query.engines)
        return params

    async def search(self, query: SearchQuery) -> SearchOutcome:
        started = time.perf_counter()
        payload = await self._get_json("/search", self._build_params(query))
        elapsed_ms = (time.perf_counter() - started) * 1000

        return self._map_outcome(query, payload, elapsed_ms)

    def _map_outcome(
        self, query: SearchQuery, payload: dict[str, Any], elapsed_ms: float
    ) -> SearchOutcome:
        results = tuple(
            mapped
            for raw in payload.get("results", [])
            if (mapped := self._map_result(raw)) is not None
        )

        failures = tuple(
            EngineFailure(engine=str(entry[0]), reason=str(entry[1]))
            for entry in payload.get("unresponsive_engines", [])
            if isinstance(entry, (list, tuple)) and len(entry) >= 2
        )

        return SearchOutcome(
            query=query,
            results=results,
            failures=failures,
            suggestions=tuple(str(s) for s in payload.get("suggestions", [])[:10]),
            answers=tuple(self._answer_text(a) for a in payload.get("answers", [])[:5]),
            corrections=tuple(str(c) for c in payload.get("corrections", [])[:5]),
            infoboxes=tuple(
                box
                for raw in payload.get("infoboxes", [])[:3]
                if (box := self._map_infobox(raw)) is not None
            ),
            upstream_ms=round(elapsed_ms, 2),
            from_cache=False,
        )

    @staticmethod
    def _answer_text(answer: Any) -> str:
        # Upstream has emitted answers both as plain strings and as objects
        # with an `answer` key, depending on the engine.
        if isinstance(answer, dict):
            return str(answer.get("answer", ""))
        return str(answer)

    def _map_result(self, raw: dict[str, Any]) -> SearchResult | None:
        """Map one upstream result, or drop it.

        Dropping is the right response to an unusable result. A result whose
        URL is a ``javascript:`` payload has nothing to render safely, and
        passing it on in the hope the frontend is careful is how SF-005 turns
        into a stored XSS.
        """
        url = str(raw.get("url") or "").strip()
        kind = _TEMPLATE_KINDS.get(str(raw.get("template", "")), ResultKind.WEB)

        # Torrent results legitimately carry magnet links, which are not http
        # but are also not dangerous to display as text.
        if kind is not ResultKind.TORRENT and not is_safe_web_url(url):
            return None

        title = str(raw.get("title") or "").strip()
        if not title:
            return None

        engines = raw.get("engines") or ([raw["engine"]] if raw.get("engine") else [])

        return SearchResult(
            url=url,
            title=title,
            snippet=str(raw.get("content") or "").strip(),
            kind=kind,
            engines=tuple(str(e) for e in engines),
            score=float(raw.get("score") or 0.0),
            published_at=self._parse_date(raw.get("publishedDate")),
            author=str(raw["author"]) if raw.get("author") else None,
            media=self._map_media(raw, kind),
            metadata=str(raw["metadata"]) if raw.get("metadata") else None,
        )

    def _map_media(self, raw: dict[str, Any], kind: ResultKind) -> Media | None:
        img_src = str(raw.get("img_src") or "")
        thumbnail = str(raw.get("thumbnail") or raw.get("thumbnail_src") or "")

        proxied_image = self._sign_image_url(img_src) if img_src else None
        proxied_thumb = self._sign_image_url(thumbnail) if thumbnail else None

        if not proxied_image and not proxied_thumb and kind is not ResultKind.VIDEO:
            return None

        return Media(
            image_url=proxied_image,
            thumbnail_url=proxied_thumb or proxied_image,
            duration_s=self._parse_duration(raw.get("length")),
            image_format=str(raw["img_format"]) if raw.get("img_format") else None,
        )

    @staticmethod
    def _parse_date(value: Any) -> datetime | None:
        if not value or not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _parse_duration(value: Any) -> int | None:
        """Parse an upstream duration, which arrives in inconsistent forms.

        Observed as ``"3:42"``, as a bare number of seconds, and as ``None``.
        Anything unrecognised yields ``None`` rather than a guess.
        """
        if value in (None, "", "None"):
            return None
        text = str(value)
        if ":" in text:
            try:
                parts = [int(p) for p in text.split(":")]
            except ValueError:
                return None
            seconds = 0
            for part in parts:
                seconds = seconds * 60 + part
            return seconds
        try:
            return int(float(text))
        except ValueError:
            return None

    def _map_infobox(self, raw: dict[str, Any]) -> Infobox | None:
        title = str(raw.get("infobox") or raw.get("title") or "").strip()
        if not title:
            return None

        url = ""
        urls = raw.get("urls") or []
        if urls and isinstance(urls[0], dict):
            candidate = str(urls[0].get("url") or "")
            url = candidate if is_safe_web_url(candidate) else ""

        attributes = tuple(
            (str(a.get("label", "")), str(a.get("value", "")))
            for a in (raw.get("attributes") or [])[:8]
            if isinstance(a, dict) and a.get("label")
        )

        return Infobox(
            title=title,
            content=str(raw.get("content") or "").strip(),
            url=url or None,
            image_url=self._sign_image_url(str(raw.get("img_src") or "")),
            attributes=attributes,
        )

    # ------------------------------------------------------------------
    # Suggestions, engines, health
    # ------------------------------------------------------------------

    async def suggest(self, text: str) -> tuple[str, ...]:
        """Autocomplete, best-effort.

        Never raises. A failing suggestion backend must not break the search
        box, and there is nothing useful a caller could do with the error
        anyway — the correct behaviour is simply no suggestions.
        """
        if not text.strip():
            return ()
        try:
            response = await self._client.get(
                f"{self._base_url}/autocompleter",
                params={"q": text, "format": "json"},
                timeout=2.0,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            log.info("suggest_unavailable", error_type=type(exc).__name__)
            return ()

        # Upstream returns OpenSearch style: [query, [suggestions...]]
        if isinstance(payload, list) and len(payload) >= 2 and isinstance(payload[1], list):
            return tuple(str(s) for s in payload[1][:10])
        if isinstance(payload, list):
            return tuple(str(s) for s in payload[:10] if isinstance(s, str))
        return ()

    async def engines(self) -> tuple[EngineInfo, ...]:
        """Engine capabilities, read from live upstream config.

        Read rather than hardcoded so the interface cannot advertise a filter
        an engine does not support, and cannot rot when upstream changes an
        engine's capabilities or the operator edits settings.yml.
        """
        payload = await self._get_json("/config", {})
        return tuple(
            EngineInfo(
                name=str(e.get("name", "")),
                categories=tuple(str(c) for c in e.get("categories", [])),
                enabled=bool(e.get("enabled", False)),
                shortcut=str(e.get("shortcut") or ""),
                supports_paging=bool(e.get("paging", False)),
                supports_time_range=bool(e.get("time_range_support", False)),
                supports_safesearch=bool(e.get("safesearch", False)),
                timeout_s=float(e.get("timeout") or 0.0),
            )
            for e in payload.get("engines", [])
            if e.get("name")
        )

    async def healthy(self) -> bool:
        try:
            response = await self._client.get(f"{self._base_url}/healthz", timeout=3.0)
        except Exception:
            return False
        return response.status_code == 200

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    async def _get_json(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        """GET and decode, translating transport failures into the taxonomy.

        Retries only transport-level failures, and only when the call never
        produced a response. A response that arrived with partial results is a
        success (ADR-0006); retrying it would multiply load on already
        struggling upstream engines to re-fetch results we already hold.
        """
        url = f"{self._base_url}{path}"
        last_exc: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.get(url, params=params, timeout=self._timeout_s)
            except httpx.TimeoutException as exc:
                last_exc = exc
                log.warning("upstream_timeout", attempt=attempt, path=path)
                continue
            except httpx.HTTPError as exc:
                last_exc = exc
                log.warning(
                    "upstream_transport_error",
                    attempt=attempt,
                    path=path,
                    error_type=type(exc).__name__,
                )
                continue

            if response.status_code >= 500:
                # Server-side failure: worth one more attempt.
                last_exc = UpstreamError(f"Backend returned {response.status_code}.")
                log.warning("upstream_server_error", status=response.status_code, path=path)
                continue

            if response.status_code >= 400:
                # Client-side: retrying an identical request cannot help.
                raise UpstreamError(
                    f"Search backend rejected the request ({response.status_code})."
                )

            try:
                payload: dict[str, Any] = response.json()
            except ValueError as exc:
                raise UpstreamError("Search backend returned a malformed response.") from exc

            return payload

        if isinstance(last_exc, httpx.TimeoutException):
            raise UpstreamTimeoutError from last_exc
        raise UpstreamUnavailableError from last_exc
