"""SearXNG adapter: response mapping, signing, and failure translation."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import httpx
import pytest
import respx

from tests.conftest import SEARXNG_BASE, TEST_SECRET, searxng_response
from veilix.core.errors import UpstreamError, UpstreamTimeoutError, UpstreamUnavailableError
from veilix.domain.models import ResultKind, SearchQuery
from veilix.providers.searxng import IMAGE_PROXY_PATH, SearxngProvider, classify_failure


def _query(**kw: Any) -> SearchQuery:
    return SearchQuery(text=kw.pop("text", "test"), **kw)


class TestResultMapping:
    @respx.mock
    async def test_maps_a_basic_result(self, provider: SearxngProvider) -> None:
        respx.get(f"{SEARXNG_BASE}/search").mock(
            return_value=httpx.Response(200, json=searxng_response())
        )

        outcome = await provider.search(_query())

        assert len(outcome.results) == 1
        result = outcome.results[0]
        assert result.url == "https://example.com/article"
        assert result.title == "An Example Result"
        assert result.kind is ResultKind.WEB
        assert result.engines == ("mojeek", "qwant")
        assert result.display_domain == "example.com"

    @respx.mock
    async def test_reports_engine_failures_without_failing(self, provider: SearxngProvider) -> None:
        """Partial results are a success (ADR-0006).

        This is the ordinary case on a self-hosted instance, not an edge case:
        a live probe had brave, duckduckgo and startpage all suspended on the
        first query while 20 usable results still came back.
        """
        respx.get(f"{SEARXNG_BASE}/search").mock(
            return_value=httpx.Response(
                200,
                json=searxng_response(
                    unresponsive=[
                        ["brave", "Suspended: too many requests"],
                        ["duckduckgo", "CAPTCHA"],
                    ]
                ),
            )
        )

        outcome = await provider.search(_query())

        assert len(outcome.results) == 1, "results still returned"
        assert outcome.is_degraded is True
        assert {f.engine for f in outcome.failures} == {"brave", "duckduckgo"}

    @respx.mock
    async def test_drops_results_with_dangerous_urls(self, provider: SearxngProvider) -> None:
        # A hostile result must not become a clickable javascript: link (SF-005).
        respx.get(f"{SEARXNG_BASE}/search").mock(
            return_value=httpx.Response(
                200,
                json=searxng_response(
                    results=[
                        {
                            "url": "javascript:alert(document.cookie)",
                            "title": "Malicious",
                            "content": "",
                            "engine": "evil",
                            "engines": ["evil"],
                            "template": "default.html",
                            "score": 9.0,
                        },
                        {
                            "url": "https://good.example/page",
                            "title": "Legitimate",
                            "content": "",
                            "engine": "mojeek",
                            "engines": ["mojeek"],
                            "template": "default.html",
                            "score": 1.0,
                        },
                    ]
                ),
            )
        )

        outcome = await provider.search(_query())

        assert len(outcome.results) == 1
        assert outcome.results[0].url == "https://good.example/page"

    @respx.mock
    async def test_drops_results_without_a_title(self, provider: SearxngProvider) -> None:
        respx.get(f"{SEARXNG_BASE}/search").mock(
            return_value=httpx.Response(
                200,
                json=searxng_response(
                    results=[
                        {
                            "url": "https://example.com",
                            "title": "",
                            "content": "",
                            "engine": "x",
                            "engines": ["x"],
                            "template": "default.html",
                        }
                    ]
                ),
            )
        )
        assert len((await provider.search(_query())).results) == 0

    @pytest.mark.parametrize(
        ("template", "expected"),
        [
            ("default.html", ResultKind.WEB),
            ("images.html", ResultKind.IMAGE),
            ("videos.html", ResultKind.VIDEO),
            ("torrent.html", ResultKind.TORRENT),
            ("paper.html", ResultKind.PAPER),
            ("unknown-template.html", ResultKind.WEB),
        ],
    )
    @respx.mock
    async def test_kind_comes_from_template(
        self, provider: SearxngProvider, template: str, expected: ResultKind
    ) -> None:
        respx.get(f"{SEARXNG_BASE}/search").mock(
            return_value=httpx.Response(
                200,
                json=searxng_response(
                    results=[
                        {
                            "url": "https://example.com/x",
                            "title": "T",
                            "content": "",
                            "engine": "e",
                            "engines": ["e"],
                            "template": template,
                        }
                    ]
                ),
            )
        )
        outcome = await provider.search(_query())
        assert outcome.results[0].kind is expected


class TestImageProxySigning:
    """Verifies the mechanism behind the image-proxy privacy claim.

    Enabling `image_proxy` upstream is not enough: it rewrites only SearXNG's
    own HTML rendering. A live probe measured 0 of 264 JSON image results as
    proxied, so the API does the rewrite itself.
    """

    @respx.mock
    async def test_rewrites_image_urls_to_a_signed_proxy_path(
        self, provider: SearxngProvider
    ) -> None:
        original = "https://cdn.example.com/photo.jpg"
        respx.get(f"{SEARXNG_BASE}/search").mock(
            return_value=httpx.Response(
                200,
                json=searxng_response(
                    results=[
                        {
                            "url": "https://example.com/page",
                            "title": "Photo",
                            "content": "",
                            "engine": "e",
                            "engines": ["e"],
                            "template": "images.html",
                            "img_src": original,
                        }
                    ]
                ),
            )
        )

        media = (await provider.search(_query())).results[0].media
        assert media is not None
        assert media.image_url is not None

        # Never a third-party URL: that is the whole point.
        assert not media.image_url.startswith("http")
        assert media.image_url.startswith(f"{IMAGE_PROXY_PATH}?")

        expected = hmac.new(TEST_SECRET.encode(), original.encode(), hashlib.sha256).hexdigest()
        assert expected in media.image_url

    @respx.mock
    async def test_refuses_to_sign_internal_addresses(self, provider: SearxngProvider) -> None:
        """The signing-oracle guard for SF-003.

        A hostile page that ranks in results could carry an img_src aimed at
        the cloud metadata endpoint. Signing it would have SearXNG — which does
        have egress — fetch it on the attacker's behalf.
        """
        respx.get(f"{SEARXNG_BASE}/search").mock(
            return_value=httpx.Response(
                200,
                json=searxng_response(
                    results=[
                        {
                            "url": "https://example.com/page",
                            "title": "Looks innocent",
                            "content": "",
                            "engine": "e",
                            "engines": ["e"],
                            "template": "images.html",
                            "img_src": "http://169.254.169.254/latest/meta-data/",
                        }
                    ]
                ),
            )
        )

        media = (await provider.search(_query())).results[0].media
        assert media is None or media.image_url is None


class TestFailureTranslation:
    @respx.mock
    async def test_timeout_becomes_upstream_timeout(self, provider: SearxngProvider) -> None:
        respx.get(f"{SEARXNG_BASE}/search").mock(side_effect=httpx.ReadTimeout("too slow"))
        with pytest.raises(UpstreamTimeoutError):
            await provider.search(_query())

    @respx.mock
    async def test_connection_error_becomes_unavailable(self, provider: SearxngProvider) -> None:
        respx.get(f"{SEARXNG_BASE}/search").mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(UpstreamUnavailableError):
            await provider.search(_query())

    @respx.mock
    async def test_client_error_is_not_retried(self, provider: SearxngProvider) -> None:
        # Retrying an identical request that was rejected cannot help, and
        # would just add load.
        route = respx.get(f"{SEARXNG_BASE}/search").mock(return_value=httpx.Response(400))
        with pytest.raises(UpstreamError):
            await provider.search(_query())
        assert route.call_count == 1

    @respx.mock
    async def test_malformed_json_is_an_upstream_error(self, provider: SearxngProvider) -> None:
        respx.get(f"{SEARXNG_BASE}/search").mock(
            return_value=httpx.Response(200, text="<html>not json</html>")
        )
        with pytest.raises(UpstreamError):
            await provider.search(_query())

    @respx.mock
    async def test_retries_transport_failures_when_configured(
        self, http_client: httpx.AsyncClient
    ) -> None:
        provider = SearxngProvider(
            http_client, base_url=SEARXNG_BASE, secret=TEST_SECRET, max_retries=2
        )
        route = respx.get(f"{SEARXNG_BASE}/search").mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(UpstreamUnavailableError):
            await provider.search(_query())
        assert route.call_count == 3, "initial attempt plus two retries"


class TestSuggestionsAndEngines:
    @respx.mock
    async def test_parses_opensearch_suggestions(self, provider: SearxngProvider) -> None:
        respx.get(f"{SEARXNG_BASE}/autocompleter").mock(
            return_value=httpx.Response(200, json=["priv", ["privacy", "private dns"]])
        )
        assert await provider.suggest("priv") == ("privacy", "private dns")

    @respx.mock
    async def test_suggestions_never_raise(self, provider: SearxngProvider) -> None:
        # A failing autocomplete must not break the search box someone is
        # typing into.
        respx.get(f"{SEARXNG_BASE}/autocompleter").mock(side_effect=httpx.ConnectError("down"))
        assert await provider.suggest("priv") == ()

    async def test_empty_input_skips_the_call(self, provider: SearxngProvider) -> None:
        assert await provider.suggest("   ") == ()

    @respx.mock
    async def test_reads_engine_capabilities_from_live_config(
        self, provider: SearxngProvider
    ) -> None:
        respx.get(f"{SEARXNG_BASE}/config").mock(
            return_value=httpx.Response(
                200,
                json={
                    "engines": [
                        {
                            "name": "mojeek",
                            "categories": ["general"],
                            "enabled": True,
                            "shortcut": "mjk",
                            "paging": True,
                            "time_range_support": False,
                            "safesearch": False,
                            "timeout": 4.0,
                        }
                    ]
                },
            )
        )
        engines = await provider.engines()
        assert len(engines) == 1
        assert engines[0].name == "mojeek"
        assert engines[0].supports_paging is True
        assert engines[0].supports_time_range is False

    @respx.mock
    async def test_health_never_raises(self, provider: SearxngProvider) -> None:
        respx.get(f"{SEARXNG_BASE}/healthz").mock(side_effect=httpx.ConnectError("x"))
        assert await provider.healthy() is False


class TestFailureClassification:
    @pytest.mark.parametrize(
        ("reason", "expected"),
        [
            ("CAPTCHA", "captcha"),
            ("Suspended: CAPTCHA", "captcha"),
            ("Suspended: too many requests", "rate_limited"),
            ("HTTP error 403 access denied", "access_denied"),
            ("timeout", "timeout"),
            ("something nobody predicted", "other"),
        ],
    )
    def test_folds_free_text_into_bounded_labels(self, reason: str, expected: str) -> None:
        # Free text as a metric label is unbounded cardinality, which is how a
        # time-series database gets killed by its own monitoring.
        assert classify_failure(reason) == expected
