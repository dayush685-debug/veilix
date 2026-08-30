"""Security controls, tested at the HTTP boundary.

Each test corresponds to a control that would fail silently if broken — the
service would keep returning 200s while the protection did nothing. That is
what makes them worth asserting rather than trusting.
"""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from tests.conftest import SEARXNG_BASE, searxng_response
from veilix.core.config import Settings
from veilix.core.security import generate_api_key, hash_api_key, hash_password

ADMIN_PASSWORD = "an-admin-password-1234"  # noqa: S105


def _basic(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


class TestRateLimiting:
    @respx.mock
    async def test_enforces_the_configured_budget(self, client: httpx.AsyncClient) -> None:
        respx.get(f"{SEARXNG_BASE}/search").mock(
            return_value=httpx.Response(200, json=searxng_response())
        )

        statuses = [
            (await client.get("/api/v1/search", params={"q": "flood"})).status_code
            for _ in range(8)
        ]

        assert statuses.count(200) == 5, "limit of 5 should be honoured exactly"
        assert statuses.count(429) == 3

    @respx.mock
    async def test_429_carries_retry_after_and_problem_json(
        self, client: httpx.AsyncClient
    ) -> None:
        respx.get(f"{SEARXNG_BASE}/search").mock(
            return_value=httpx.Response(200, json=searxng_response())
        )
        for _ in range(6):
            response = await client.get("/api/v1/search", params={"q": "flood"})

        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) > 0
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["type"].endswith("/rate-limited")

    @respx.mock
    async def test_health_endpoints_stay_reachable_under_limit(
        self, client: httpx.AsyncClient
    ) -> None:
        """Probes must answer while the limiter sheds load.

        Otherwise an orchestrator reads 429 as unhealthy and restarts a service
        that is behaving exactly as designed under load — turning a traffic
        spike into an outage.
        """
        respx.get(f"{SEARXNG_BASE}/search").mock(
            return_value=httpx.Response(200, json=searxng_response())
        )
        respx.get(f"{SEARXNG_BASE}/healthz").mock(return_value=httpx.Response(200))

        for _ in range(8):
            await client.get("/api/v1/search", params={"q": "flood"})

        assert (await client.get("/api/v1/live")).status_code == 200
        assert (await client.get("/api/v1/ready")).status_code == 200
        assert (await client.get("/api/v1/metrics")).status_code == 200

    @respx.mock
    async def test_forged_forwarded_header_cannot_buy_a_fresh_budget(
        self, client: httpx.AsyncClient
    ) -> None:
        """The failure mode this prevents is invisible.

        If X-Forwarded-For were trusted unconditionally, an attacker would set
        a new value per request, every request would land in its own bucket,
        and the limiter would keep reporting healthy numbers while limiting
        nothing at all.

        Settings here are `testing`, so the header is not trusted.
        """
        respx.get(f"{SEARXNG_BASE}/search").mock(
            return_value=httpx.Response(200, json=searxng_response())
        )

        statuses = [
            (
                await client.get(
                    "/api/v1/search",
                    params={"q": "spoof"},
                    headers={"X-Forwarded-For": f"10.0.0.{i}"},
                )
            ).status_code
            for i in range(8)
        ]

        assert 429 in statuses, "rotating X-Forwarded-For must not evade the limiter"


class TestApiKeyAuthentication:
    @respx.mock
    async def test_valid_key_raises_the_limit(
        self, app: object, client: httpx.AsyncClient, settings: Settings
    ) -> None:
        key = generate_api_key()
        app.state.settings = settings.model_copy(  # type: ignore[attr-defined]
            update={"api_key_hashes": hash_api_key(key)}
        )
        respx.get(f"{SEARXNG_BASE}/search").mock(
            return_value=httpx.Response(200, json=searxng_response())
        )

        response = await client.get("/api/v1/search", params={"q": "x"}, headers={"X-API-Key": key})

        assert response.status_code == 200
        assert response.headers["X-RateLimit-Limit"] == "50"

    @respx.mock
    async def test_invalid_key_falls_back_to_the_anonymous_budget(
        self, app: object, client: httpx.AsyncClient, settings: Settings
    ) -> None:
        # An unrecognised key must not grant elevated limits. Search is public,
        # so the request still succeeds — it just gets no privileges.
        app.state.settings = settings.model_copy(  # type: ignore[attr-defined]
            update={"api_key_hashes": hash_api_key(generate_api_key())}
        )
        respx.get(f"{SEARXNG_BASE}/search").mock(
            return_value=httpx.Response(200, json=searxng_response())
        )

        response = await client.get(
            "/api/v1/search", params={"q": "x"}, headers={"X-API-Key": "vlx_forged"}
        )

        assert response.headers["X-RateLimit-Limit"] == "5"


class TestAdminAuthentication:
    @pytest.fixture
    def admin_app(self, app: object, settings: Settings) -> object:
        app.state.settings = settings.model_copy(  # type: ignore[attr-defined]
            update={
                "admin_username": "admin",
                "admin_password_hash": hash_password(ADMIN_PASSWORD),
            }
        )
        return app

    async def test_rejects_missing_credentials(
        self, admin_app: object, client: httpx.AsyncClient
    ) -> None:
        response = await client.get("/api/v1/admin/overview")
        assert response.status_code == 401
        assert response.json()["type"].endswith("/authentication-required")

    @pytest.mark.parametrize(
        ("username", "password"),
        [
            ("admin", "wrong-password"),
            ("root", ADMIN_PASSWORD),
            ("", ""),
            ("admin", ""),
        ],
    )
    async def test_rejects_bad_credentials(
        self,
        admin_app: object,
        client: httpx.AsyncClient,
        username: str,
        password: str,
    ) -> None:
        response = await client.get("/api/v1/admin/overview", headers=_basic(username, password))
        assert response.status_code == 401

    async def test_accepts_correct_credentials(
        self, admin_app: object, client: httpx.AsyncClient
    ) -> None:
        response = await client.get(
            "/api/v1/admin/overview", headers=_basic("admin", ADMIN_PASSWORD)
        )
        assert response.status_code == 200

    async def test_locked_when_no_admin_configured(
        self, app: object, client: httpx.AsyncClient, settings: Settings
    ) -> None:
        # Fails closed: an unconfigured deployment gets a locked door, not a
        # lobby.
        app.state.settings = settings.model_copy(  # type: ignore[attr-defined]
            update={"admin_password_hash": ""}
        )
        response = await client.get(
            "/api/v1/admin/overview", headers=_basic("admin", ADMIN_PASSWORD)
        )
        assert response.status_code == 401

    async def test_overview_exposes_no_user_data(
        self, admin_app: object, client: httpx.AsyncClient
    ) -> None:
        """The dashboard must not become a surveillance tool with a login page."""
        body = (
            await client.get("/api/v1/admin/overview", headers=_basic("admin", ADMIN_PASSWORD))
        ).json()

        serialised = str(body).lower()
        for forbidden in ("query", "search_text", "client_ip", "ip_address", "user_id"):
            assert forbidden not in serialised, f"{forbidden} must not appear"


class TestResultSanitisation:
    @respx.mock
    async def test_dangerous_result_urls_never_reach_the_client(
        self, client: httpx.AsyncClient
    ) -> None:
        """SF-005: result fields are authored by whoever ranks for a query."""
        respx.get(f"{SEARXNG_BASE}/search").mock(
            return_value=httpx.Response(
                200,
                json=searxng_response(
                    results=[
                        {
                            "url": scheme,
                            "title": "Hostile",
                            "content": "",
                            "engine": "e",
                            "engines": ["e"],
                            "template": "default.html",
                        }
                        for scheme in (
                            "javascript:alert(1)",
                            "data:text/html,<script>alert(1)</script>",
                            "file:///etc/passwd",
                        )
                    ]
                ),
            )
        )

        body = (await client.get("/api/v1/search", params={"q": "x"})).json()

        assert body["count"] == 0
        assert all(r["url"].startswith(("http://", "https://")) for r in body["results"])

    @respx.mock
    async def test_image_urls_are_proxied_not_third_party(self, client: httpx.AsyncClient) -> None:
        """The rendered page must not fetch from third-party image hosts."""
        respx.get(f"{SEARXNG_BASE}/search").mock(
            return_value=httpx.Response(
                200,
                json=searxng_response(
                    results=[
                        {
                            "url": "https://example.com/p",
                            "title": "Image",
                            "content": "",
                            "engine": "e",
                            "engines": ["e"],
                            "template": "images.html",
                            "img_src": "https://tracker.example.net/pixel.jpg",
                        }
                    ]
                ),
            )
        )

        body = (await client.get("/api/v1/search", params={"q": "x"})).json()
        media = body["results"][0]["media"]

        assert media["image_url"].startswith("/img?")
        assert "tracker.example.net" not in media["image_url"].split("?")[0]


class TestResponseHeaders:
    @respx.mock
    async def test_request_id_is_returned_and_unique_per_request(
        self, client: httpx.AsyncClient
    ) -> None:
        respx.get(f"{SEARXNG_BASE}/search").mock(
            return_value=httpx.Response(200, json=searxng_response())
        )
        first = await client.get("/api/v1/search", params={"q": "a"})
        second = await client.get("/api/v1/search", params={"q": "b"})

        assert first.headers["X-Request-ID"]
        # Not a session identifier: two requests must share nothing, or it
        # becomes a tracking token by accident.
        assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]

    async def test_hostile_request_id_is_replaced(self, client: httpx.AsyncClient) -> None:
        # Echoing arbitrary client bytes into every log line invites log
        # injection and forged entries.
        injected = "abc\ndef INJECTED-LOG-LINE"
        response = await client.get("/api/v1/live", headers={"X-Request-ID": injected})
        assert response.headers["X-Request-ID"] != injected
        assert response.headers["X-Request-ID"].isalnum()
