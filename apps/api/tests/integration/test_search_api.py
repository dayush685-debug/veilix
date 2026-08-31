"""API-level behaviour with the upstream mocked.

These exercise the real routing, middleware ordering, validation, exception
handlers, and serialisation. Only SearXNG is faked, so what is under test is
the part we wrote.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from tests.conftest import SEARXNG_BASE, searxng_response


class TestSearchEndpoint:
    @respx.mock
    async def test_returns_results(self, client: httpx.AsyncClient) -> None:
        respx.get(f"{SEARXNG_BASE}/search").mock(
            return_value=httpx.Response(200, json=searxng_response())
        )

        response = await client.get("/api/v1/search", params={"q": "python"})

        assert response.status_code == 200
        body = response.json()
        assert body["query"] == "python"
        assert body["count"] == 1
        assert body["results"][0]["title"] == "An Example Result"
        assert body["results"][0]["domain"] == "example.com"

    @respx.mock
    async def test_response_has_no_fabricated_total(self, client: httpx.AsyncClient) -> None:
        """Guards a deliberate omission.

        Upstream returned `number_of_results: null` on a live probe, so any
        web-scale total would be invented. If someone later adds one, this
        fails and they have to justify where the number came from.
        """
        respx.get(f"{SEARXNG_BASE}/search").mock(
            return_value=httpx.Response(200, json=searxng_response())
        )
        body = (await client.get("/api/v1/search", params={"q": "x"})).json()

        assert "total_results" not in body
        assert "total" not in body
        assert body["count"] == len(body["results"])

    @respx.mock
    async def test_partial_failure_is_a_200_with_degraded_flag(
        self, client: httpx.AsyncClient
    ) -> None:
        respx.get(f"{SEARXNG_BASE}/search").mock(
            return_value=httpx.Response(
                200,
                json=searxng_response(
                    unresponsive=[["brave", "CAPTCHA"], ["duckduckgo", "timeout"]]
                ),
            )
        )

        response = await client.get("/api/v1/search", params={"q": "x"})

        assert response.status_code == 200, "partial results are a success"
        body = response.json()
        assert body["degraded"] is True
        assert len(body["failures"]) == 2
        assert body["count"] == 1

    @respx.mock
    async def test_reports_timing_and_cache_state(self, client: httpx.AsyncClient) -> None:
        respx.get(f"{SEARXNG_BASE}/search").mock(
            return_value=httpx.Response(200, json=searxng_response())
        )

        first = (await client.get("/api/v1/search", params={"q": "cache-me"})).json()
        assert first["timing"]["cached"] is False
        assert first["timing"]["upstream_ms"] is not None

        second = (await client.get("/api/v1/search", params={"q": "cache-me"})).json()
        assert second["timing"]["cached"] is True

    @respx.mock
    async def test_cache_key_distinguishes_parameters(self, client: httpx.AsyncClient) -> None:
        """A cache that ignores a parameter returns confidently wrong results."""
        route = respx.get(f"{SEARXNG_BASE}/search").mock(
            return_value=httpx.Response(200, json=searxng_response())
        )

        await client.get("/api/v1/search", params={"q": "same"})
        await client.get("/api/v1/search", params={"q": "same", "page": 2})
        await client.get("/api/v1/search", params={"q": "same", "safesearch": 2})
        await client.get("/api/v1/search", params={"q": "same", "category": "news"})

        assert route.call_count == 4, "each parameter set must miss cache"

    @respx.mock
    async def test_upstream_timeout_maps_to_504_problem_json(
        self, client: httpx.AsyncClient
    ) -> None:
        respx.get(f"{SEARXNG_BASE}/search").mock(side_effect=httpx.ReadTimeout("slow"))

        response = await client.get("/api/v1/search", params={"q": "x"})

        assert response.status_code == 504
        assert response.headers["content-type"].startswith("application/problem+json")
        body = response.json()
        assert body["type"].endswith("/upstream-timeout")
        assert body["request_id"]

    @respx.mock
    async def test_upstream_down_maps_to_503(self, client: httpx.AsyncClient) -> None:
        respx.get(f"{SEARXNG_BASE}/search").mock(side_effect=httpx.ConnectError("refused"))
        response = await client.get("/api/v1/search", params={"q": "x"})
        assert response.status_code == 503


class TestValidation:
    @pytest.mark.parametrize(
        ("params", "why"),
        [
            ({}, "missing query"),
            ({"q": ""}, "empty query"),
            ({"q": "x" * 600}, "query over the length cap"),
            ({"q": "x", "page": 0}, "page below 1"),
            ({"q": "x", "page": 99}, "page over the cap"),
            ({"q": "x", "category": "not-a-category"}, "unknown category"),
            ({"q": "x", "safesearch": 5}, "safesearch out of range"),
            ({"q": "x", "language": "english"}, "language not a code"),
            ({"q": "x", "time_range": "decade"}, "unknown time range"),
            ({"q": "x", "engines": "a;drop table"}, "engines with illegal characters"),
            ({"q": "x", "unexpected": "1"}, "unknown parameter"),
        ],
    )
    async def test_rejects_bad_input(
        self, client: httpx.AsyncClient, params: dict[str, object], why: str
    ) -> None:
        response = await client.get("/api/v1/search", params=params)

        assert response.status_code == 422, f"should reject: {why}"
        # One error shape across the whole API, including validation failures.
        assert response.headers["content-type"].startswith("application/problem+json")
        body = response.json()
        assert body["type"].endswith("/invalid-request")
        assert body["errors"], "the caller needs to know which parameter was wrong"

    @respx.mock
    async def test_accepts_valid_optional_parameters(self, client: httpx.AsyncClient) -> None:
        respx.get(f"{SEARXNG_BASE}/search").mock(
            return_value=httpx.Response(200, json=searxng_response())
        )
        response = await client.get(
            "/api/v1/search",
            params={
                "q": "x",
                "category": "news",
                "page": 2,
                "language": "en-US",
                "safesearch": 2,
                "time_range": "week",
            },
        )
        assert response.status_code == 200


class TestSuggestions:
    @respx.mock
    async def test_returns_suggestions(self, client: httpx.AsyncClient) -> None:
        respx.get(f"{SEARXNG_BASE}/autocompleter").mock(
            return_value=httpx.Response(200, json=["pri", ["privacy", "private"]])
        )
        body = (await client.get("/api/v1/search/suggestions", params={"q": "pri"})).json()
        assert body["suggestions"] == ["privacy", "private"]

    @respx.mock
    async def test_upstream_failure_yields_empty_list_not_an_error(
        self, client: httpx.AsyncClient
    ) -> None:
        respx.get(f"{SEARXNG_BASE}/autocompleter").mock(side_effect=httpx.ConnectError("down"))
        response = await client.get("/api/v1/search/suggestions", params={"q": "pri"})
        assert response.status_code == 200
        assert response.json()["suggestions"] == []


class TestOperationsEndpoints:
    async def test_liveness_does_not_touch_dependencies(self, client: httpx.AsyncClient) -> None:
        # No respx mock registered: any upstream call would fail the test,
        # which is the point, liveness must not depend on anything.
        response = await client.get("/api/v1/live")
        assert response.status_code == 200
        assert response.json() == {"status": "alive"}

    @respx.mock
    async def test_readiness_reports_components(self, client: httpx.AsyncClient) -> None:
        respx.get(f"{SEARXNG_BASE}/healthz").mock(return_value=httpx.Response(200))
        body = (await client.get("/api/v1/ready")).json()
        assert body["ready"] is True
        assert {c["name"] for c in body["components"]} == {"searxng", "valkey"}

    @respx.mock
    async def test_readiness_fails_when_search_backend_is_down(
        self, client: httpx.AsyncClient
    ) -> None:
        respx.get(f"{SEARXNG_BASE}/healthz").mock(side_effect=httpx.ConnectError("down"))
        response = await client.get("/api/v1/ready")
        assert response.status_code == 503
        assert response.json()["ready"] is False

    @respx.mock
    async def test_metrics_exposes_prometheus_text(self, client: httpx.AsyncClient) -> None:
        respx.get(f"{SEARXNG_BASE}/search").mock(
            return_value=httpx.Response(200, json=searxng_response())
        )
        await client.get("/api/v1/search", params={"q": "metric-me"})

        response = await client.get("/api/v1/metrics")

        assert response.status_code == 200
        assert "veilix_search_requests_total" in response.text
        assert "veilix_http_requests_total" in response.text


class TestOpenApi:
    async def test_schema_is_generated(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/openapi.json")
        assert response.status_code == 200

        schema = response.json()
        paths = schema["paths"]
        for expected in (
            "/api/v1/search",
            "/api/v1/search/suggestions",
            "/api/v1/engines",
            "/api/v1/health",
            "/api/v1/ready",
            "/api/v1/live",
            "/api/v1/admin/overview",
        ):
            assert expected in paths, f"{expected} missing from OpenAPI"

    async def test_error_responses_are_documented(self, client: httpx.AsyncClient) -> None:
        schema = (await client.get("/openapi.json")).json()
        responses = schema["paths"]["/api/v1/search"]["get"]["responses"]
        assert "429" in responses
        assert "503" in responses
