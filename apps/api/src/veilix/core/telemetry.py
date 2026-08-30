"""Metrics and tracing.

The governing rule from docs/privacy.md §8: **no metric label may carry a
user-derived value.** No IP, no hashed IP, no query text, no session ID.

That is not only a privacy rule, it is also what keeps Prometheus healthy —
per-user labels are precisely the unbounded-cardinality mistake that melts a
time-series database. The privacy property and the operational property have
the same enforcement point, which is why labels here are drawn only from small
fixed sets: route templates, engine names, and outcome enums.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

if TYPE_CHECKING:
    from fastapi import FastAPI

# A private registry rather than the global default. The default registry is
# process-global mutable state: importing a module twice under pytest raises
# "Duplicated timeseries", and any dependency can quietly publish into it.
REGISTRY: Final = CollectorRegistry()

# Buckets chosen for this workload rather than copied from a template. A live
# probe measured general search at 1.7-5.0s, so the interesting resolution is
# between 100ms (a cache hit) and 10s (the outer timeout). Sub-50ms buckets
# would waste series on a range only health checks occupy.
_LATENCY_BUCKETS: Final = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 10.0)

# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

http_requests_total: Final = Counter(
    "veilix_http_requests_total",
    "HTTP requests handled.",
    # `route` is the route TEMPLATE ("/api/v1/search"), never the resolved URL,
    # which would carry the query string and therefore the user's query.
    labelnames=("method", "route", "status_class"),
    registry=REGISTRY,
)

http_request_duration_seconds: Final = Histogram(
    "veilix_http_request_duration_seconds",
    "Wall-clock duration of HTTP request handling.",
    labelnames=("method", "route"),
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Search orchestration
# ---------------------------------------------------------------------------

search_requests_total: Final = Counter(
    "veilix_search_requests_total",
    "Search requests by category and outcome.",
    labelnames=("category", "outcome"),
    registry=REGISTRY,
)

search_duration_seconds: Final = Histogram(
    "veilix_search_duration_seconds",
    "End-to-end search duration, including cache lookup and upstream call.",
    labelnames=("category", "cache"),
    buckets=_LATENCY_BUCKETS,
    registry=REGISTRY,
)

search_results_returned: Final = Histogram(
    "veilix_search_results_returned",
    "Number of results returned per search.",
    buckets=(0, 1, 5, 10, 20, 50, 100, 200, 500),
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Upstream engine health
#
# Sourced from SearXNG's `unresponsive_engines`, which reports what actually
# happened to real queries (ADR-0006). Engine names come from a fixed upstream
# set, so cardinality stays bounded.
# ---------------------------------------------------------------------------

engine_failures_total: Final = Counter(
    "veilix_engine_failures_total",
    "Upstream engine failures, as reported by SearXNG per search.",
    labelnames=("engine", "reason"),
    registry=REGISTRY,
)

engine_results_total: Final = Counter(
    "veilix_engine_results_total",
    "Results contributed per upstream engine.",
    labelnames=("engine",),
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Cache and rate limiting
# ---------------------------------------------------------------------------

cache_operations_total: Final = Counter(
    "veilix_cache_operations_total",
    "Cache lookups by outcome.",
    labelnames=("outcome",),  # hit | miss | error | disabled
    registry=REGISTRY,
)

ratelimit_events_total: Final = Counter(
    "veilix_ratelimit_events_total",
    "Rate limiter decisions.",
    labelnames=("identity", "decision"),  # anonymous|api_key , allowed|blocked
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

breaker_state: Final = Gauge(
    "veilix_breaker_state",
    "Circuit breaker state: 0=closed, 1=half_open, 2=open.",
    labelnames=("dependency",),
    registry=REGISTRY,
)

breaker_transitions_total: Final = Counter(
    "veilix_breaker_transitions_total",
    "Circuit breaker state transitions.",
    labelnames=("dependency", "to_state"),
    registry=REGISTRY,
)

# ---------------------------------------------------------------------------
# Build info
# ---------------------------------------------------------------------------

build_info: Final = Gauge(
    "veilix_build_info",
    "Build and runtime metadata, always 1. Labels carry the information.",
    labelnames=("version", "environment"),
    registry=REGISTRY,
)


def status_class(status_code: int) -> str:
    """Bucket a status code into a low-cardinality label.

    Exact codes would multiply series for little operational gain; the
    question a dashboard asks is "are we serving 5xx", not "how many 418s".
    """
    return f"{status_code // 100}xx"


def setup_tracing(app: FastAPI, *, endpoint: str, service_name: str = "veilix-api") -> bool:
    """Enable OpenTelemetry export when a collector endpoint is configured.

    Tracing is instrumented but not exported by default. Running a collector
    costs two containers, which is not free on a small host, and an operator
    who has not set up a backend gains nothing from spans emitted into a void.
    Setting the endpoint is the single switch that turns it on.

    Returns whether tracing was enabled, so startup can log the fact rather
    than leaving an operator wondering.
    """
    if not endpoint:
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        # The otel extra is optional; a missing dependency must not stop the
        # service from serving search.
        return False

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)

    # excluded_urls keeps health and metrics scrapes out of the trace stream;
    # they would otherwise dominate it without carrying information.
    FastAPIInstrumentor.instrument_app(
        app, tracer_provider=provider, excluded_urls="health,ready,live,metrics"
    )
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)
    return True
