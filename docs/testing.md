# Testing Strategy

> **287 tests**: 226 backend, 17 frontend component, 44 end-to-end
> (22 specs × chromium and mobile). Backend line coverage **89%**.

## The principle

**Test what would be silently wrong.**

This project has now produced the same class of bug six times: configuration
that looks correct, passes review, passes unit tests, and does nothing. A proxy
that appends a header where the code expects it replaced. Environment variables
the container never received. An OpenTelemetry extra missing from an image.
Traces exporting search queries. A Prometheus port mapping that was inert. A
keyboard shortcut that silently did nothing on half the site.

None of those are logic errors. Every one of them was found by **running the
real thing and looking at what came out** — and every one was invisible to a
test that mocked the layer where the bug lived.

That shapes the pyramid here: it is wider at the top than orthodoxy prescribes,
because integration boundaries are where this system actually breaks.

## Layers

| Layer | Count | Runs against | Catches |
|---|---|---|---|
| Unit | 181 | Pure functions, fakes | Logic, edge cases, security predicates |
| Integration | 27 | FastAPI + `respx` + `fakeredis` | Wiring, contracts, error mapping |
| Security | 18 | The API surface | Auth, limits, validation, SSRF guards |
| Component | 17 | jsdom | ARIA contracts, hostile-content escaping |
| End-to-end | 44 | **The real running stack** | Everything the above cannot see |
| Verification | 38 | Live containers | Architectural claims (`verify-stack.sh`) |

`scripts/verify-stack.sh` is not conventionally a test suite, and it is the most
valuable safety net in the repository. It asserts the claims the ADRs make —
that the backend network has no internet route, that only the edge publishes a
port, that access logs contain nothing IP-shaped — against containers that are
actually running.

## What is deliberately not tested

Being explicit is part of a strategy; an untested area that nobody named is an
oversight, one that is named is a decision.

- **`setup_tracing`'s SDK wiring** (telemetry.py, 62% covered). Testing it would
  mean asserting that OpenTelemetry does what OpenTelemetry does. The part that
  is *ours* — query redaction — has 9 dedicated tests, and the wiring was
  verified end to end against a real collector.
- **`main.py` lifespan** (62%). Exercised by every integration and E2E test; a
  unit test would assert that startup calls the functions startup calls.
- **Frontend page components** (52% statement coverage, and the honest number).
  Component tests cover `SearchBar` and `ResultCard`, where correctness is
  subtle and invisible — ARIA relationships and hostile-content escaping. Page
  composition is covered by E2E against the real stack instead, because a jsdom
  test of a page that mocks its own API mostly asserts that React renders. The
  low number is a deliberate placement of effort, not a gap nobody noticed.
- **Load beyond ~50 concurrent requests.** The host is a 2.77 GiB Docker VM
  shared with unrelated workloads; numbers past that would measure the laptop.

## End-to-end tests

Run against the **real deployed stack** — no mocked API, no stubbed SearXNG.

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
cd tests/e2e && npx playwright test
```

**Assertions are about behaviour, never about results.** Upstream engines
CAPTCHA and rate-limit this instance constantly, so "the page shows results for
X" is a flaky assertion about someone else's infrastructure. "The page renders a
coherent state given whatever came back" is an assertion about our code. Tests
accept results *or* a well-formed empty state, and check the disclosure is
honest when degradation appears.

Serial, one worker: the stack is a single instance with rate limiting on, and
parallel workers would trip the limiter and fail each other.

No retries locally. A test that passes on retry is a flake, and hiding it is how
a real intermittent bug stays hidden. CI retries once, because upstream engines
genuinely are unreliable.

Both a desktop and a mobile viewport run, because the layout is mobile-first and
that claim should be tested rather than asserted.

### What the E2E suite found

**A real bug.** Pressing `/` on `/about` or `/privacy` did nothing, because
those pages have no search box — while the footer advertised "Press / to search
from anywhere". It now falls back to the home page. No unit test would have
caught this: the handler was correct, the *site* was inconsistent.

**A genuine design tension.** The skip link could not be the first tab stop on
the home page, because the home page autofocuses the search box, so forward
tabbing starts *after* the link that precedes it in the DOM. Two accessibility
affordances in conflict. The resolution: on Home the search box *is* the main
content, so skipping to it is what the skip link would have done anyway; on
content pages, where the link earns its place, it comes first. The test now
asserts that, and documents why.

**Five bugs in my own tests** — an ambiguous "Search" locator that also matched
"Clear search", a `combobox` role that also matched the filter `<select>`s, an
invalid CSS selector, `check()` against a deliberately `sr-only` input, and a
URL assertion expecting `+` where `encodeURIComponent` emits `%20`. Worth
listing because "the test failed" and "the code is broken" are different
findings, and conflating them erodes trust in the suite.

## Security tests

18 tests over the controls that would be catastrophic to get wrong:

- API key auth: missing, wrong, malformed, and **unconfigured fails closed**
- Admin auth: wrong username still runs the password verification, so timing
  does not reveal which half was wrong
- Rate limiting: enforcement, headers, and that health endpoints are exempt
- Input validation: query length, page bounds, unknown parameters rejected
- SSRF guards: scheme allowlist, private IP literals, and **single-label
  hostnames** — the gap that let `http://valkey:6379/` through

Plus, at the stack level, `verify-stack.sh` proves the API container has no
internet route and that SearXNG is unreachable through the edge — checked by
*response body*, because the SPA fallback also answers 200 and status alone
gives a false pass.

## Coverage, and why 89% rather than higher

The brief asks for meaningful coverage rather than chasing 100%, and the
remaining 11% is where that principle bites. It is concentrated in three places:
the OTel SDK wiring above, lifespan startup, and defensive `except` branches for
failures that require a broken SDK to reach.

Where coverage *is* high, it is because the code is load-bearing:

| Module | Coverage | Why it matters |
|---|---|---|
| `services/search_service.py` | 100% | Cache/breaker ordering — a mistake serves stale or wrong results |
| `infrastructure/ratelimit.py` | 100% | Abuse prevention, and privacy of the IP hash |
| `core/security.py` | 100% | Every authentication path |
| `domain/models.py` | 100% | Cache fingerprinting — a missed field serves the wrong query's results |
| `schemas/search.py` | 100% | The public API contract |

## Running everything

```bash
# Backend
cd apps/api && pytest tests -q --cov

# Frontend
cd apps/web && npx vitest run && npx tsc --noEmit && npx eslint .

# End-to-end (needs the stack up)
cd tests/e2e && npx playwright test

# Architectural claims (needs the stack up)
./scripts/verify-stack.sh

# Dependency and container vulnerabilities
PYTHON=/path/to/venv/python ./scripts/security-scan.sh
```

## Test doubles, and what each one is for

- **`respx`** intercepts httpx, so provider tests exercise the real mapping code
  against recorded upstream payloads — including the malformed and partial ones
  a live probe actually produced.
- **`fakeredis`** gives real Valkey semantics without a container, so rate-limit
  and cache tests run in milliseconds on any machine.
- **Hand-written fakes** (`BrokenRedis`, `FakeSpan`) where the point is failure
  behaviour and a mocking framework would add ceremony without clarity.

No test mocks the layer it is testing. A test that mocks SearXNG to verify the
SearXNG adapter tests only the mock — and that is precisely how every bug listed
at the top of this document would have escaped.
