"""URL safety checks.

These guard SF-005 (attacker-authored result URLs reaching a browser) and
SF-003 (the API as a signing oracle for the image proxy).
"""

from __future__ import annotations

import pytest

from veilix.core.urls import is_known_scheme, is_safe_to_proxy, is_safe_web_url


class TestSafeWebUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com",
            "http://example.com/path?q=1#frag",
            "https://sub.domain.example.co.uk/a/b",
            "https://example.com:8443/path",
        ],
    )
    def test_accepts_ordinary_web_urls(self, url: str) -> None:
        assert is_safe_web_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "JavaScript:alert(1)",
            "  javascript:alert(1)  ",
            "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
            "vbscript:msgbox(1)",
            "file:///etc/passwd",
            "about:blank",
        ],
    )
    def test_rejects_script_and_local_schemes(self, url: str) -> None:
        # Any of these in a result's `url` becomes a clickable link. The
        # allowlist stops them at the API boundary so the frontend does not
        # have to be the only thing standing between a hostile result and a
        # user's browser.
        assert is_safe_web_url(url) is False

    @pytest.mark.parametrize("url", ["", "   ", "not a url", "//example.com"])
    def test_rejects_malformed(self, url: str) -> None:
        assert is_safe_web_url(url) is False

    def test_rejects_absurdly_long_urls(self) -> None:
        assert is_safe_web_url("https://example.com/" + "a" * 4000) is False

    def test_known_scheme_allows_magnet(self) -> None:
        # Legitimate in the torrent category, but never renderable as an href
        # or feedable to the image proxy.
        assert is_known_scheme("magnet:?xt=urn:btih:abc") is True
        assert is_safe_web_url("magnet:?xt=urn:btih:abc") is False


class TestSafeToProxy:
    """Stricter than link safety: signing causes a *fetch* from a host with egress."""

    def test_accepts_public_hostname(self) -> None:
        assert is_safe_to_proxy("https://images.example.com/a.jpg") is True

    def test_accepts_public_literal_ip(self) -> None:
        assert is_safe_to_proxy("https://93.184.216.34/a.jpg") is True

    @pytest.mark.parametrize(
        ("url", "why"),
        [
            ("http://169.254.169.254/latest/meta-data/", "cloud metadata endpoint"),
            ("http://127.0.0.1:8000/api/v1/admin/overview", "loopback"),
            ("http://10.0.0.5/internal", "private class A"),
            ("http://192.168.1.1/router", "private class C"),
            ("http://172.16.0.9/internal", "private class B"),
            ("http://[::1]/local", "IPv6 loopback"),
            ("http://[fe80::1]/link-local", "IPv6 link-local"),
            ("http://0.0.0.0/", "unspecified"),
        ],
    )
    def test_rejects_internal_addresses(self, url: str, why: str) -> None:
        assert is_safe_to_proxy(url) is False, f"should reject {why}"

    @pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "gopher://x/"])
    def test_rejects_non_web_schemes(self, url: str) -> None:
        assert is_safe_to_proxy(url) is False

    def test_hostname_resolving_internally_is_not_caught(self) -> None:
        """Documents the known limit of this check rather than pretending it is complete.

        A hostname is accepted because the API container has no external DNS to
        resolve it with — the same isolation that contains an attacker (ADR-0004)
        also prevents this function from checking where a name points. Closing
        the gap needs an egress policy on the fetching side; tracked as SF-003.

        This test exists so that if someone later adds resolution, they see a
        deliberate decision here rather than assuming an oversight.
        """
        assert is_safe_to_proxy("https://internal.corp.example/secret.jpg") is True
