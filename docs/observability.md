# Observability

Three signals, one rule that governs all of them: **operational telemetry never
becomes a record of what people searched for.** That rule is enforced by
machinery at each layer rather than by care, because care does not survive a
growing codebase — and every layer here has already been caught leaking once.

## Quick start

```bash
docker compose --profile observability up -d          # + Prometheus & Grafana
docker compose -f docker-compose.yml -f docker-compose.dev.yml \
               --profile observability up -d          # + Grafana on :13000
```

Grafana's password comes from `GRAFANA_ADMIN_PASSWORD`, and compose refuses to
start the profile without one.

**Why opt-in.** Prometheus and Grafana together use ~233 MiB against ~265 MiB
for the entire core stack — measured, not estimated. An instance serves search
perfectly well without them, so they should not be a precondition for running
the product.

## Logs

Structured JSON via structlog. The schema is deliberately narrow: timestamp,
level, logger, request ID, method, **route template**, status, duration.

Enforcement is a processor that runs on *every* event and redacts a denylist of
keys — `q`, `query`, `client_ip`, `authorization`, `cookie`, and the rest. A
developer who logs a query sees `<redacted>` in the output rather than shipping
it. Discipline is not the control; the processor is.

Deliberately absent: query text, full URLs, client addresses, request bodies,
`User-Agent`, `Referer`, `Cookie`.

The request ID correlates lines *within one request*. It is regenerated per
request and cannot join two requests together, which is what stops it becoming
a tracking token by accident.

**The edge log leaked once.** Caddy's access log carried `client_ip` on every
line, because the filter deleted `remote_ip` — the name Caddy 1 used. The
configuration looked right and logged every visitor's address. Found by reading
output, not config (SF-008). `scripts/verify-stack.sh` now greps recent access
logs for anything IP-shaped.

## Metrics

Prometheus-compatible, at `/api/v1/metrics`. Pull-based on purpose: a push agent
would need an outbound path the API container deliberately does not have
(ADR-0004), and it would ship telemetry off-box.

**No metric label may carry a user-derived value.** This is both the privacy
rule and the thing that keeps a time-series database alive — per-user labels are
the unbounded-cardinality mistake that melts Prometheus. One enforcement point,
two problems solved.

Enforced as an **allowlist** of label names, not a denylist of bad ones. A
denylist catches only the leaks someone already imagined; an allowlist fails on
any new label until a human approves it. The 14 approved names are route,
method, status_class, category, outcome, cache, engine, reason, identity,
decision, dependency, to_state, version, environment, plus histogram `le`.

Alert rules live in `infra/prometheus/rules/`. Four alerts, each describing a
condition someone would act on — an alert nobody acts on trains people to ignore
alerts. Two recording rules precompute expressions the dashboard reads on every
refresh.

## Tracing

OpenTelemetry, instrumented always, **exporting only when
`VEILIX_OTLP_ENDPOINT` is set**. No collector ships by default.

Verified end to end against a real collector: a traced search produced a server
span `GET /api/v1/search` with child sends and httpx client spans, tagged
`service.name=veilix-api`.

Three things went wrong getting there, and all three were invisible from inside
the application:

**1. The SDK was not in the image.** The Dockerfile installed the package
without its `[otel]` extra, so setting the endpoint did nothing at all — a
documented configuration knob that silently lied. The import failure now logs
loudly instead of returning `False` in silence.

**2. Server spans were missing.** `instrument_app` was called from lifespan,
which runs after the application object is assembled. Starlette builds its
middleware stack once, so the instrumentation middleware never made it in:
outgoing httpx spans appeared and HTTP server spans did not. Tracing looked
enabled and was half missing. Setup moved into the app factory.

**3. Traces leaked search queries.** This is the serious one. OpenTelemetry's
HTTP instrumentations record the full request URL by default, so a traced search
exported

```
http.url = http://searxng:8080/search?q=<the user's query>&format=json...
```

to whatever backend an operator had configured. Caught with a canary query
against a live collector.

It is worse than an ordinary logging mistake because nobody thinks of a tracing
backend as somewhere search history accumulates — someone enables tracing to
debug latency and starts shipping queries. A span processor now strips
everything after `?` from `http.url` and `url.full`, keeping the path, which is
useful and carries no user data. Pinned by
`tests/unit/test_telemetry_redaction.py`, because the symptom is invisible from
inside the process.

One implementation note worth keeping: the processor was first written as a
duck-typed class and **broke tracing outright** — the SDK calls a private
`_on_ending` hook on every processor, so a class that merely looks like a
`SpanProcessor` raises inside `span.end()`. Structural typing is not enough when
the protocol has private members. It subclasses the real base class now.

## Health endpoints

Three endpoints answering three different questions, because conflating them is
expensive:

| Endpoint | Question | Touches dependencies |
|---|---|---|
| `/api/v1/live` | Is this process running? | **No** |
| `/api/v1/ready` | Should traffic route here? | Yes |
| `/api/v1/health` | Operator detail | Yes |

If readiness failures triggered restarts, a brief Valkey outage would become a
restart loop across every replica at once. Measured during the Phase 6 failure
test: with SearXNG stopped, `/ready` returned 503 while `/live` stayed 200
throughout.

All four are exempt from rate limiting — an orchestrator reading a `429` as
"unhealthy" would restart a service behaving exactly as designed under load.

## Dashboards

`infra/grafana/dashboards/veilix-operations.json`, provisioned from the file so
a fresh Grafana comes up already wired. `allowUiUpdates` is off: the JSON is
version-controlled, so an edit in the browser would diverge from the repository
and be lost on the next deploy.

Panel choices worth noting:

- **Latency and request rate are separate panels**, not one panel with two
  y-axes. Two measures of different scale on one plot invite false correlation,
  and the shared time axis already lets you compare them.
- **Breaker state uses text value mappings** (`CLOSED` / `HALF-OPEN` / `OPEN`),
  so state is never carried by colour alone.
- **Cache hit ratio shows "no lookups yet"** rather than 0% before any traffic.
  Those mean different things, and rendering them identically sends someone off
  debugging a healthy cache.
- **Engine health is a bar gauge, not a time series** — the question is a ranked
  comparison right now, not a trend.

## The admin dashboard, and what it cannot do

`/admin` in the SPA, behind HTTP Basic over TLS, reading
`/api/v1/admin/overview`.

Every figure is an aggregate already present in the Prometheus registry, so the
page **cannot display something the metrics endpoint does not**. There is no
view of individual searches, query text, or client addresses, and no way to add
one without changing what the system collects.

That is the difference between an operations dashboard and a surveillance tool
with a login page, and it is structural rather than a matter of what got built.

## Network placement

| Service | Networks | Published |
|---|---|---|
| Prometheus | `backend` only | **Never** |
| Grafana | `backend` + `edge` | dev only, loopback |

Prometheus has no authentication whatsoever, so anything that can reach it can
read every metric. Being on an `internal: true` network makes that structural
rather than a matter of remembering — the network has no gateway, so a `ports:`
entry would be silently inert even if someone added one. Humans reach the data
through Grafana, which proxies queries over the internal network.

Inspect Prometheus directly with:

```bash
docker exec veilix-prometheus wget -qO- http://127.0.0.1:9090/api/v1/targets
```

## What is checked automatically

`scripts/verify-stack.sh` asserts, among 38 checks:

- Prometheus is scraping all targets and every rule evaluates cleanly
- Prometheus is **not** reachable from the host
- Grafana is up and rejects unauthenticated requests
- every metric label is on the approved list
- edge access logs contain nothing IP-shaped
