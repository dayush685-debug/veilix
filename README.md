# Veilix

A self-hostable meta-search platform built around
[SearXNG](https://github.com/searxng/searxng). It forwards a query to many
search engines, merges the results, and keeps no search history, accounts, or
identity cookies.

```
Browser ─▶ Caddy (TLS, CSP, serves the SPA) ─┬─▶ FastAPI (validate, limit, orchestrate)
                                             │      ├─▶ Valkey  (cache, rate-limit counters)
                                             │      └─▶ SearXNG (─▶ upstream engines)
                                             └─▶ /img ─▶ SearXNG image proxy
```

There is no hosted demo and no screenshot in this repository. The quick start
below brings the whole stack up locally in one command.

## Status of claims

Everything below is labelled so you can tell what has been exercised from what
has only been designed.

| Label | Meaning |
|---|---|
| **Verified** | Measured, or asserted by a test in this repository |
| **Designed** | Implemented and reasoned about, not measured under load |
| **Not verified** | Built but not yet exercised in its target environment |

## Capabilities

- Search across web, news, images, videos, code, science, music, maps and files
- Category tabs, time-range and safe-search filters, server-side autocomplete
- Typed REST API with generated OpenAPI and RFC 9457 error responses
- Prometheus metrics, Grafana dashboards, OpenTelemetry tracing
- Operations page showing aggregate health and no user data

## Architecture

Four containers in the core profile, two more behind `--profile observability`.
Three Docker networks: `edge`, `backend` (`internal: true`), and `egress`.

The property that shapes the rest: **the API container has no route to the
internet.** It sits on `edge` and `backend` only, and `backend` has no gateway.
SearXNG is the single container on `egress`, because it is the only one that
needs to reach upstream engines. *Verified* by `scripts/verify-stack.sh`, which
runs a container on `backend` and confirms it cannot resolve `example.com`.

Full detail, including where TLS terminates and where rate limiting and caching
happen, is in [docs/architecture.md](docs/architecture.md).

## Privacy model

The system has no user accounts, no identity cookies, and no table, key or file
holding search history.

Rate limiting has to tell clients apart, which is the one place an IP address is
unavoidable. The raw address is used only as input to
`HMAC-SHA256(ip, daily_salt)` and is never written to Valkey, a log, or a metric
label. The salt is derived from a seed and the UTC date, so after rotation the
previous day's buckets cannot be tied back to an address.

**Veilix does not make you anonymous, and this repository does not claim it
does.** Caddy terminates TLS and the API handles every query in plaintext to do
its job, so whoever operates the machine can observe traffic if they choose to.
[docs/privacy.md](docs/privacy.md) traces what each layer can technically see,
including the timing side channel a shared cache creates.

## Security model

*Verified* by `scripts/verify-stack.sh` and `scripts/security-scan.sh`:

| Control | Status |
|---|---|
| Containers running as root | 0 of 6 |
| Read-only root filesystem | 6 of 6 |
| `cap_drop: ALL` + `no-new-privileges` | 6 of 6 |
| Services publishing a host port | 1 (the edge) |
| Fixable HIGH/CRITICAL image CVEs | 0 |

Search results are third-party content and are treated as untrusted input at
three layers: URL scheme allowlisting in the API, text-only rendering in the
frontend, and a CSP with `script-src 'self'` plus one hash.

14 findings are recorded in
[docs/security-findings.md](docs/security-findings.md): 10 fixed, 2 mitigated,
1 partially mitigated, 1 accepted with an expiry date. The two that are not
closed appear under Known limitations.

## Performance

*Verified* on a 12-core laptop with a 2.77 GiB Docker VM shared with unrelated
containers. Method in [docs/performance.md](docs/performance.md).

| Measure | Result |
|---|---|
| Search latency, cache hit | 4.96 ms median, 10.37 ms p95 |
| Search latency, cold | 795 ms median, 5.94 s p95 |
| Sustained throughput | 15.2 req/s |
| Rate limiter at 149 req/s offered | 92.6% rejected, 0 errors |
| Core stack memory, idle | 187 MiB across four containers |
| Core stack memory, after sustained load | 414 MiB |
| Frontend bundle | 89 kB JavaScript gzipped, 95 kB with CSS and HTML |

Cold latency is dominated by upstream engines, not by this code. The throughput
figure measures the laptop it ran on and is not a capacity claim.

## Technology

Python 3.13, FastAPI, Pydantic, React 19, TypeScript, Vite, Tailwind 4, SearXNG,
Valkey, Caddy, Docker Compose, Prometheus, Grafana, OpenTelemetry, pytest,
Vitest, Playwright, Ruff, mypy, GitHub Actions.

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

## Configuration

All configuration is environment-driven and validated at startup.
`.env.example` documents all 21 settings, and CI fails if the code reads one the
example does not document.

In production the API refuses to start with a placeholder secret, a missing
admin hash, rate limiting disabled, or wildcard CORS. Each of those produces an
instance that runs, reports healthy, and is insecure.

## Production deployment

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Caddy obtains certificates automatically, so `VEILIX_SITE_ADDRESS` must be a
real hostname with public DNS and ports 80 and 443 reachable.

*Verified locally* using Caddy's internal CA: HTTPS 200, HTTP 308 redirect,
search returning results over TLS, all four containers healthy.
**Not verified**: certificate issuance against Let's Encrypt, which needs a
public domain.

Backup is one file. `.env` holds every secret and is the only irreplaceable
thing; there is no database to restore. See
[docs/deployment.md](docs/deployment.md).

## Testing

```bash
cd apps/api && pytest tests -q --cov     # 225 tests, 89% coverage
cd apps/web && npx vitest run            # 17 component tests
cd tests/e2e && npx playwright test      # 88, against the real stack
./scripts/verify-stack.sh                # 38 architectural assertions
./scripts/security-scan.sh               # pip-audit, npm audit, Trivy
```

330 tests in total. The end-to-end suite runs against real containers instead of
mocks, because the recurring failure in this project was configuration that
looked correct and did nothing. [docs/testing.md](docs/testing.md) covers the
strategy and what is not tested.

## Observability

```bash
docker compose --profile observability up -d
```

Opt-in: Prometheus and Grafana together cost roughly as much memory as the core
stack. Prometheus publishes no port and has no authentication, so reach it
through Grafana or an SSH tunnel. See
[docs/observability.md](docs/observability.md).

## Known limitations

- **Upstream engines block self-hosted instances.** Several are usually
  CAPTCHA-blocked at any moment, worse from datacentre IPs. Results pages name
  which ones. This is inherent to self-hosting, not a defect here.
- **Single replica only.** *Designed* for horizontal scaling — the API is
  stateless and Valkey is shared — but the circuit breaker is per-process and
  the rate-limit salt would need sharing. Neither has been built or measured.
- **No zero-downtime deploys** on one host. Expect a few seconds of 502 while
  the API container is replaced.
- **SF-003, partially mitigated.** The image-proxy guard rejects private IP
  literals and internal hostnames, but a public hostname whose DNS resolves to a
  private address still passes. The API has no external DNS, so it cannot check;
  closing this needs an egress policy on SearXNG.
- **SF-009, accepted.** 14 Go CVEs are compiled into the upstream Caddy binary.
  The newest release is built against a Go version that still carries them, so
  they cannot be patched from here. Recorded in `.trivyignore` with an expiry
  date.
- No accessibility conformance claim. Semantics are correct by construction
  and asserted by tests, but no contrast measurement or screen-reader pass has
  been run.
- **CI has not run on GitHub.** Every step was executed locally; the workflow
  itself is *not verified*.

## Roadmap

- Egress policy for SearXNG, to close SF-003
- Shared rate-limit salt and breaker state, as a precondition for replicas
- Automated accessibility auditing
- Optional local semantic search behind the existing `SearchProvider` interface

## Documentation

| Document | Covers |
|---|---|
| [architecture.md](docs/architecture.md) | Topology, request flow, technology decisions |
| [privacy.md](docs/privacy.md) | Data inventory, retention, operator visibility |
| [security.md](docs/security.md) | Implemented controls and configuration |
| [threat-model.md](docs/threat-model.md) | Actors, trust boundaries, residual risk |
| [security-findings.md](docs/security-findings.md) | All 14 findings with evidence |
| [performance.md](docs/performance.md) | Measured latency, throughput, failure behaviour |
| [observability.md](docs/observability.md) | Logs, metrics, tracing, dashboards |
| [testing.md](docs/testing.md) | Strategy and deliberate omissions |
| [deployment.md](docs/deployment.md) | Production deploy, backup and recovery |
| [api.md](docs/api.md) | API reference |
| [adr/](docs/adr/) | Six decision records |

## Contributing

Run `ruff`, `mypy`, `eslint`, `tsc`, the test suites, and
`./scripts/verify-stack.sh` before opening a pull request. CI runs all of them.

Report security issues through a private advisory, not a public issue.

## Licence

MIT, see [LICENSE](LICENSE).

SearXNG is AGPL-3.0-or-later. Veilix runs it as an unmodified upstream container
image and communicates over HTTP, without linking to or modifying its source.
The LICENSE file records this; it describes how the projects are combined and is
not legal advice.
