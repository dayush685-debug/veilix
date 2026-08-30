# API

Base path `/api/v1`. The authoritative reference is the generated OpenAPI
document at `/openapi.json`, with human-readable docs at `/redoc` (and `/docs`
outside production). This page covers what a schema cannot: the behaviours a
client has to design around.

## Two things that will surprise you

**1. Partial results are a success, not an error.**

Upstream engines routinely CAPTCHA and rate-limit self-hosted instances. A live
probe on a clean instance had `brave`, `duckduckgo`, and `startpage` all
suspended on the very first query, and still returned 20 usable results. This is
the steady state, not an incident.

So a `200` may still report failed engines:

```json
{
  "count": 38,
  "degraded": true,
  "failures": [
    {"engine": "brave", "reason": "timeout"},
    {"engine": "duckduckgo", "reason": "CAPTCHA"},
    {"engine": "startpage", "reason": "Suspended: CAPTCHA"}
  ],
  "engines_used": ["google cse", "mojeek", "qwant"]
}
```

Check `degraded`. Do not assume an empty `failures` array.

**2. There is no total result count.**

A live probe measured the upstream returning `number_of_results: null`, so any
web-scale total would be invented. `count` is the number of results on the page
you asked for. An integration test asserts no `total` field ever appears, so
that this stays true.

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/v1/search` | none | Aggregated search |
| GET | `/api/v1/search/suggestions` | none | Autocomplete |
| GET | `/api/v1/engines` | none | Engine catalogue and capabilities |
| GET | `/api/v1/live` | none | Liveness probe |
| GET | `/api/v1/ready` | none | Readiness probe |
| GET | `/api/v1/health` | none | Operator health detail |
| GET | `/api/v1/metrics` | none | Prometheus metrics |
| GET | `/api/v1/admin/overview` | Basic | Aggregate operations dashboard |

### GET /api/v1/search

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `q` | string, 1–512 chars | required | |
| `category` | enum | `general` | `general`, `news`, `images`, `videos`, `it`, `science`, `files`, `map`, `music`, `social media` |
| `page` | int 1–10 | `1` | |
| `language` | `auto`, `en`, or `en-US` | `auto` | |
| `safesearch` | `0`, `1`, `2` | `1` | off, moderate, strict |
| `time_range` | `day`,`week`,`month`,`year` | none | |
| `engines` | comma-separated names | none | Restrict to specific engines |

**Unknown parameters are rejected with 422.** This is deliberate: a typo such as
`safe_search=2` would otherwise be silently ignored, and the caller would
receive moderate filtering while believing they had asked for strict.

```bash
curl "https://your-instance/api/v1/search?q=distributed+systems&category=general"
```

### Timing

Every search reports where the time went, so a client can tell a slow upstream
from a busy server:

```json
{"timing": {"total_ms": 1.23, "upstream_ms": 5092.47, "cached": true}}
```

## Authentication

**API keys** — send `X-API-Key`. Keys raise the rate limit
(600/min versus 60/min by default). Search itself is public: an unrecognised key
does not fail the request, it simply grants no privileges.

Keys are 256-bit random values stored as SHA-256 digests. They are deliberately
*not* Argon2-hashed — slow hashing defends low-entropy secrets against offline
cracking, and a random 256-bit key has no dictionary to defend against. Using
Argon2 here would add ~50 ms of CPU to every authenticated request, which an
attacker triggers for free by sending garbage keys.

**Admin** — HTTP Basic over TLS. The password *is* human-chosen, so it is
Argon2id-hashed. Generate credentials with:

```bash
python scripts/hash_secret.py --admin-password
python scripts/hash_secret.py --api-key
```

## Rate limiting

Sliding window, per client. Anonymous callers are identified by
`HMAC-SHA256(ip, daily_salt)` — the raw IP is never stored anywhere, and the
salt rotates daily, so buckets become permanently unlinkable to any address
(ADR-0003).

Every response carries `X-RateLimit-Limit` and `X-RateLimit-Remaining`, so a
client can self-throttle rather than discovering the limit by hitting it.
Exceeding it returns `429` with `Retry-After`.

Health, readiness, liveness, and metrics are exempt — otherwise an orchestrator
would read a `429` as unhealthy and restart a service that is behaving exactly
as designed under load.

## Errors

Every error is RFC 9457 `application/problem+json`, validation failures
included:

```json
{
  "type": "https://veilix.dev/problems/rate-limited",
  "title": "Too Many Requests",
  "status": 429,
  "detail": "Request budget exhausted. Retry after the indicated interval.",
  "request_id": "9f2c1b7e4a6d",
  "retry_after": 42
}
```

| Status | `type` suffix | Meaning |
|---|---|---|
| 422 | `invalid-request` | Parameter validation failed; see the `errors` array |
| 401 | `authentication-required` | Missing or invalid credential |
| 429 | `rate-limited` | Budget exhausted; honour `Retry-After` |
| 502 | `upstream-error` | Backend returned something unusable |
| 503 | `upstream-unavailable` | Backend unreachable |
| 503 | `circuit-open` | Veilix is shedding load while the backend recovers |
| 504 | `upstream-timeout` | Backend did not answer in time |

`circuit-open` and `upstream-unavailable` are separate on purpose. The first is
a decision Veilix made; the second is a failure the backend reported. Collapsing
them would hide the difference on a dashboard.

**500 responses carry no detail.** An unexpected exception raised while handling
a search can carry the query in its message, so nothing about the exception is
returned. Use `request_id` — present on every error and in the `X-Request-ID`
header — to find the server-side log line.

## Request correlation

Send `X-Request-ID` (alphanumeric, ≥8 chars) to correlate your logs with ours,
or one is generated. It is regenerated per request and is **not** a session
identifier — two requests from the same client share nothing, which is what
stops it becoming a tracking token by accident. Non-conforming values are
replaced rather than echoed, since echoing client bytes into log lines invites
log injection.

## Image results

`media.image_url` is always a Veilix proxy path (`/img?url=…&h=…`), never a
third-party URL. Rendering a results page therefore never connects the viewer's
browser to `gstatic.com`, `pinimg.com`, or anywhere else they did not choose.

This is done by the API rather than by the upstream setting: `image_proxy: true`
only rewrites SearXNG's own HTML rendering, and a live probe measured **0 of 264
JSON image results** as proxied. The API re-signs each URL with the shared
secret. A verification check asserts that no third-party image URL ever appears
in a response.

URLs pointing at private, loopback, or link-local addresses are refused rather
than signed (SF-003). The residual risk — a *hostname* that resolves internally
— is documented in [`security-findings.md`](./security-findings.md) rather than
claimed as solved.

## Stability

`/api/v1` is versioned in the path. Fields may be added; existing fields will
not change meaning without a version bump. `total_results` will not appear,
because the number does not exist.
