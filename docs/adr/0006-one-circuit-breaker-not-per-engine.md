# ADR-0006: One circuit breaker around SearXNG, not per upstream engine

- **Status**: Accepted
- **Date**: 2026-08-30

## Context

The brief asks for timeouts, retries, circuit breakers, provider health tracking, and
graceful degradation. The naive reading is to implement per-provider circuit breakers in
the orchestration layer — one breaker per search engine, tracking failures and opening
when an engine misbehaves.

Probing the actual SearXNG image shows that layer already exists upstream, and is more
sophisticated than a reimplementation would be. Its defaults:

```yaml
ban_time_on_fail: 5           # seconds
max_ban_time_on_fail: 120
suspended_times:
  SearxEngineAccessDenied: 180     # HTTP 402, 403
  SearxEngineCaptcha: 3600
  SearxEngineTooManyRequests: 180  # HTTP 429
  cf_SearxEngineCaptcha: 1296000   # Cloudflare, 15 days
  cf_SearxEngineAccessDenied: 86400
```

This is per-engine failure classification with error-type-specific back-off — a
CAPTCHA-walled engine is suspended for an hour, a Cloudflare-blocked one for fifteen
days, a merely rate-limited one for three minutes. A live probe confirmed it working:
`brave`, `duckduckgo`, and `startpage` were all suspended on the first query, and the
request still returned twenty usable results.

Building a second breaker layer above this would mean two independent state machines with
different notions of "failed", each reacting to the other's decisions. Our layer cannot
even observe individual engine calls — it sees one HTTP response from SearXNG — so it
would be guessing at state that upstream already tracks precisely.

## Decision

**Do not implement per-engine circuit breakers.** Rely on SearXNG's engine suspension,
and tune its thresholds through configuration.

Implement exactly one circuit breaker, around the **SearXNG dependency itself**. It
opens when SearXNG as a whole is failing — connection refused, timeouts, 5xx — and
protects the API from queueing requests against a wedged or overloaded instance.

Derive provider health from `unresponsive_engines`, the per-engine failure list
already present in every SearXNG JSON response. This is measured data from real queries,
not a synthetic health check that would itself consume upstream quota.

## Consequences

**Positive.** One state machine instead of two, at the only level where our layer has
real signal. Engine health reporting reflects what actually happened to user queries. We
inherit upstream's error taxonomy, which already distinguishes CAPTCHA from rate limit
from access denied — a distinction we would otherwise have to rediscover.

**Negative.** Engine back-off behaviour is tuned through SearXNG's configuration rather
than our code, so that tuning lives in a different file and a different mental model from
the rest of the orchestration logic. Engine health is only observed for engines that
recent queries actually touched; an engine nobody has queried has unknown health rather
than known-good, and the admin dashboard must present it that way rather than implying a
green check it has not earned.

**Retry policy.** Retries apply to the SearXNG call only, are bounded, and are used only
for transport-level failures — never for a response that returned partial results.
Retrying a partial success would multiply load on already-struggling upstreams to
re-fetch results we already have.
