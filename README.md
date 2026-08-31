# Veilix

A privacy-first, self-hostable meta-search platform built around
[SearXNG](https://github.com/searxng/searxng).

It aggregates results from many search engines without accounts, without
cookies, without search history, and without building a profile of the person
asking.

```
Browser ─▶ Caddy (TLS, CSP, serves the SPA) ─┬─▶ FastAPI (validate, limit, orchestrate)
                                             │      ├─▶ Valkey  (cache, rate-limit counters)
                                             │      └─▶ SearXNG (─▶ 272 upstream engines)
                                             └─▶ /img ─▶ SearXNG image proxy
```

---

## Why this exists

Mainstream search is funded by profiling. Meta-search breaks the link between a
person and their queries by putting a server in between: upstream engines see
the instance, not the user.

The interesting engineering isn't the search box — SearXNG already does search.
It's the platform around it:

- a **rate limiter that counts clients without storing IP addresses**
- **network isolation that leaves the API container with no route to the
  internet at all**
- an orchestration layer where **partial results are a success**, because
  upstream engines fail constantly
- **server-side image proxying**, which also enables a strict `img-src 'self'` CSP
- observability that measures the system without surveilling its users

## Measured, not claimed

Every number here came from a real run. Hardware and method are in
[docs/performance.md](docs/performance.md).

| | |
|---|---|
| Search latency, cache hit | **4.96 ms** median · 10.37 ms p95 |
| Search latency, cold | **795 ms** median · 5.94 s p95 (upstream-dominated) |
| Sustained throughput | **15.2 req/s** on a shared 2.77 GiB Docker VM |
| Rate limiter under 149 req/s | **92.6%** correctly rejected, 0 errors |
| Core stack memory | **~265 MiB** across four containers |
| Frontend bundle | **88 kB** gzipped, zero third-party requests |
| Tests | **287** — 226 backend, 17 component, 44 end-to-end |
| Backend coverage | **89%** |
| Architectural assertions | **38**, run against live containers |
| Fixable HIGH/CRITICAL CVEs | **0** across both images |
| Containers running as root | **0 of 6** |

The throughput figure measures a laptop sharing a Docker VM with unrelated
projects. It is not a capacity claim.

## Failure is the normal case

A live probe against a clean SearXNG instance, on its very first query:

```
unresponsive_engines: [['brave',      'Suspended: too many requests'],
                       ['duckduckgo', 'CAPTCHA'],
                       ['startpage',  'Suspended: CAPTCHA']]
```

Three well-known engines down immediately, because upstreams rate-limit and
CAPTCHA-wall self-hosted instances. The query still returned 20 usable results.

So the system treats partial results as success: a `degraded` flag, the failed
engines named in the response, and an interface that says which sources were
unavailable rather than quietly looking confident.

---

## Quick start

```bash
git clone <this-repo> veilix && cd veilix
cp .env.example .env

python scripts/hash_secret.py --secret           # SEARXNG_SECRET and salt seed
python scripts/hash_secret.py --admin-password   # admin hash

docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
```

Open <http://localhost:8088>. The first start takes 30–60 s while SearXNG
initialises its engines.

```bash
./scripts/verify-stack.sh    # 38 assertions against the running stack
```

**Production**, with automatic HTTPS — see
[docs/deployment.md](docs/deployment.md):

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

## Local development

```bash
# Backend
cd apps/api && pip install -e ".[dev,otel]"
pytest tests -q --cov && ruff check src tests && mypy

# Frontend
cd apps/web && npm ci
npm run dev                  # proxies /api to the container
npx vitest run && npx tsc --noEmit && npx eslint .

# End-to-end, against the real stack
cd tests/e2e && npm ci && npx playwright test
```

## Features

**Search** — web, news, images, videos, code, science, music, maps, and files
across 272 engines. Category tabs, time-range and safe-search filters,
server-side autocomplete, quick answers, and related searches.

**Interface** — original design, dark mode, mobile-first, keyboard shortcuts,
and an ARIA-correct combobox. No third-party fonts, analytics, or trackers: the
page loads nothing from a host the user did not choose.

**API** — typed REST with generated OpenAPI, RFC 9457 error responses, request
IDs, and API-key authentication. See [docs/api.md](docs/api.md).

**Operations** — Prometheus metrics, Grafana dashboards, OpenTelemetry tracing,
health/readiness/liveness endpoints, and an admin page that *cannot* show user
data.

## Configuration

Everything is environment-driven and validated at startup. `.env.example`
documents all 21 settings, and CI fails if the code reads one the example does
not document.

Production refuses to start with a placeholder secret, a missing admin hash,
rate limiting disabled, or wildcard CORS — each of those produces an instance
that runs, looks healthy, and is insecure.

## API

```bash
curl "https://your-instance/api/v1/search?q=privacy&category=general"
```

Two behaviours that surprise clients, both deliberate:

- **`count` is results on this page, not a web-scale total.** The upstream
  reports no total, so any big number would be invented.
- **`degraded: true` is normal.** Check it rather than assuming `failures` is
  empty.

Interactive docs at `/redoc`, schema at `/openapi.json`.

## Testing

```bash
cd apps/api && pytest tests -q --cov     # 226 tests, 89%
cd apps/web && npx vitest run            # 17 component tests
cd tests/e2e && npx playwright test      # 44, against the real stack
./scripts/verify-stack.sh                # 38 architectural assertions
./scripts/security-scan.sh               # pip-audit, npm audit, Trivy
```

The E2E suite deliberately runs against real containers. This project produced
the same class of bug six times — configuration that looks correct, passes
review, passes unit tests, and does nothing — and every one was found by running
the real thing rather than a mock. [docs/testing.md](docs/testing.md) explains
the strategy and what is deliberately *not* tested.

## Monitoring

```bash
docker compose --profile observability up -d
```

Opt-in, because it costs ~233 MiB — roughly as much as the entire core stack.
Prometheus is never published, since it has no authentication; Grafana proxies
queries inward. Details in [docs/observability.md](docs/observability.md).

## Troubleshooting

**Every search is degraded.** Normal. Upstream engines block self-hosted
instances, worse from datacentre IPs. The results page names which ones.

**SearXNG will not become healthy.** Engine initialisation takes 30–60 s cold.
`docker logs veilix-searxng` — individual engine load failures are expected.

**Caddy will not start.** With `VEILIX_SITE_ADDRESS` set to a hostname it needs
ports 80 and 443, plus a public DNS record for certificates.

**The API refuses to start in production.** That is the configuration validator;
the log lists every problem at once. See
[docs/deployment.md §3](docs/deployment.md).

**Logs show no query text.** By design — there is no debug flag that turns it
on. Use the request ID from an error response.

## Documentation

| Document | What it covers |
|---|---|
| [architecture.md](docs/architecture.md) | Topology, request flow, technology decisions, verified SearXNG facts |
| [privacy.md](docs/privacy.md) | Complete data inventory and what the operator can still observe |
| [security.md](docs/security.md) | Implemented controls and how to configure them |
| [threat-model.md](docs/threat-model.md) | Actors, trust boundaries, residual risk |
| [security-findings.md](docs/security-findings.md) | All 14 findings with evidence — 12 fixed, 2 open |
| [performance.md](docs/performance.md) | Measured latency, throughput, failure behaviour |
| [observability.md](docs/observability.md) | Logs, metrics, tracing, dashboards |
| [testing.md](docs/testing.md) | Strategy, deliberate omissions, what E2E found |
| [deployment.md](docs/deployment.md) | Production deploy, backup and recovery |
| [api.md](docs/api.md) | API reference |
| [adr/](docs/adr/) | Six decision records, including what was rejected |

## Technology

Python 3.13 · FastAPI · Pydantic · React 19 · TypeScript · Vite · Tailwind 4 ·
SearXNG · Valkey · Caddy · Docker Compose · Prometheus · Grafana ·
OpenTelemetry · pytest · Vitest · Playwright · Ruff · mypy · GitHub Actions

## Honesty policy

This project does not claim to make anyone anonymous.
[docs/privacy.md §9](docs/privacy.md) states exactly what the operator of the
machine can still observe. Performance figures appear only when measured, with
the hardware named. Two security findings remain open and are documented with
their residual risk rather than closed quietly.

## Roadmap

Designed for but **not built** — and therefore not claimed:

- Horizontal scaling. The API is stateless and Valkey is shared, but the
  per-process circuit breaker and the rate-limit salt both need work first
  (documented in the ADRs).
- An egress policy to close SF-003 fully.
- An automated accessibility audit. Semantics are correct by construction and
  asserted by tests, but no contrast measurement or screen-reader pass has run,
  so WCAG conformance is not claimed.
- Optional local semantic search behind the existing `SearchProvider` seam.

## Contributing

Issues and pull requests welcome. Please run `ruff`, `mypy`, `eslint`, `tsc`,
the test suites, and `./scripts/verify-stack.sh` before opening one — CI runs
all of them.

Security issues: open a private advisory rather than a public issue.

## Licence

MIT — see [LICENSE](LICENSE).

SearXNG is AGPL-3.0-or-later. Veilix runs it as an **unmodified upstream
container image** and talks to it over HTTP, so this code is not a derivative
work and the AGPL's source-disclosure obligation does not reach it. Forking or
patching SearXNG itself would change that.
