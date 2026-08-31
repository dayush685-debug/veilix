"""Trace redaction.

Guards a real leak. OpenTelemetry's HTTP instrumentations record the full
request URL by default, so enabling tracing exported

    http.url = http://searxng:8080/search?q=<the user's query>&...

to whatever trace backend was configured, search queries shipped off-box by a
feature someone turned on to debug latency. Found with a canary query against a
live collector, not by reading the instrumentation source.

These tests pin the fix so it cannot regress silently, since the symptom is
invisible from inside the application.
"""

from __future__ import annotations

from typing import Any

import pytest

from veilix.core.telemetry import _redact_span_urls


class FakeSpan:
    """Minimal stand-in exposing the attribute mapping the SDK uses."""

    def __init__(self, attributes: dict[str, Any] | None) -> None:
        self._attributes = attributes


class TestUrlRedaction:
    @pytest.mark.parametrize("key", ["http.url", "url.full"])
    def test_strips_the_query_string(self, key: str) -> None:
        # Both attribute names, because the semantic conventions renamed it and
        # instrumentations in the wild still emit either.
        span = FakeSpan({key: "http://searxng:8080/search?q=secret+query&format=json"})
        _redact_span_urls(span)
        assert span._attributes is not None
        assert span._attributes[key] == "http://searxng:8080/search"
        assert "secret" not in span._attributes[key]

    def test_keeps_the_path(self) -> None:
        # The path is the useful part and carries no user data; dropping it
        # would make traces much harder to read for no privacy gain.
        span = FakeSpan({"http.url": "https://example.com/api/v1/search?q=x"})
        _redact_span_urls(span)
        assert span._attributes is not None
        assert span._attributes["http.url"] == "https://example.com/api/v1/search"

    def test_leaves_urls_without_a_query_untouched(self) -> None:
        span = FakeSpan({"http.url": "http://searxng:8080/healthz"})
        _redact_span_urls(span)
        assert span._attributes is not None
        assert span._attributes["http.url"] == "http://searxng:8080/healthz"

    def test_leaves_other_attributes_alone(self) -> None:
        span = FakeSpan(
            {
                "http.url": "http://x/search?q=leak",
                "http.method": "GET",
                "http.status_code": 200,
                "http.route": "/api/v1/search",
            }
        )
        _redact_span_urls(span)
        assert span._attributes is not None
        assert span._attributes["http.method"] == "GET"
        assert span._attributes["http.status_code"] == 200
        assert span._attributes["http.route"] == "/api/v1/search"

    def test_redacts_every_url_attribute_present(self) -> None:
        span = FakeSpan(
            {
                "http.url": "http://a/search?q=one",
                "url.full": "http://b/search?q=two",
            }
        )
        _redact_span_urls(span)
        assert span._attributes is not None
        assert span._attributes["http.url"] == "http://a/search"
        assert span._attributes["url.full"] == "http://b/search"

    @pytest.mark.parametrize("attributes", [None, {}])
    def test_tolerates_spans_without_attributes(self, attributes: dict[str, Any] | None) -> None:
        # A processor that raises breaks span.end() for every span in the
        # process, which is a far worse outcome than a missing redaction.
        _redact_span_urls(FakeSpan(attributes))

    def test_tolerates_a_non_string_url_value(self) -> None:
        span = FakeSpan({"http.url": 12345})
        _redact_span_urls(span)
        assert span._attributes is not None
        assert span._attributes["http.url"] == 12345
