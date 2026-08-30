# Veilix

A privacy-first, self-hostable meta-search platform built around
[SearXNG](https://github.com/searxng/searxng).

It aggregates results from many search engines without accounts, without cookies, without
search history, and without building a profile of the person asking.

> **Build status: Phase 7 of 10 complete.** The full stack runs as four containers that
> are all non-root, read-only-rootfs, `cap_drop: ALL`, and `no-new-privileges`. 178 tests
> pass, `scripts/verify-stack.sh` asserts **38 claims** against the running stack,
> `scripts/security-scan.sh` reports zero fixable HIGH/CRITICAL vulnerabilities, and
> performance and failure behaviour are measured in
> [docs/performance.md](docs/performance.md). Logs, metrics, tracing, dashboards and an
> operations page are in place — see [docs/observability.md](docs/observability.md). This README is filled out as each phase
> completes, and no capability is described here before it exists.

## Why this exists

Mainstream search is funded by profiling. Meta-search breaks the link between a person
and their queries by putting a server in between: upstream engines see the instance, not
the user.

The interesting engineering is not the search box — SearXNG already does search. It is
the platform around it:

- a typed API with generated OpenAPI documentation
- an orchestration layer where **partial results are a success**, because upstream
  engines fail constantly (see below)
- a rate limiter that counts clients **without storing IP addresses**
- network isolation that leaves the API container **with no route to the internet at all**
- observability that measures the system without surveilling its users

## Measured, not claimed

```
warm search (cache hit)      p50 4.96 ms   p95 10.37 ms
cold search (upstream)       p50 795 ms    p95 5.94 s
cached query, backend DOWN   86 ms — the cache is consulted before the breaker
breaker sheds a request      12 ms, versus 7.7 s waiting for a dead dependency
```

Measured on a laptop that was also running the load generator; see
[docs/performance.md §1](docs/performance.md) before quoting any of it.

## Failure is the normal case

A live probe against a clean SearXNG instance, on its very first query:

```
unresponsive_engines: [['brave',      'Suspended: too many requests'],
                       ['duckduckgo', 'CAPTCHA'],
                       ['startpage',  'Suspended: CAPTCHA']]
```

Three well-known engines down immediately, because upstreams rate-limit and CAPTCHA-wall
self-hosted instances. The query still returned 20 usable results. Designing for this is
the reliability story, and it is measured rather than imagined.

## Architecture at a glance

```
Browser → Caddy (TLS, CSP, serves the SPA) ─┬→ FastAPI (validate, limit, orchestrate)
                                            │      ├→ Valkey  (cache, rate-limit counters)
                                            │      └→ SearXNG (→ 272 upstream engines)
                                            └→ /img → SearXNG image proxy
```

Three Docker networks. The `backend` network is `internal: true`, so the API container
has no internet route; SearXNG is the only container with egress.

Full detail in **[docs/architecture.md](docs/architecture.md)**.

## Documentation

| Document | What it covers |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Topology, request flow, technology decisions, verified SearXNG facts |
| [docs/privacy.md](docs/privacy.md) | Complete data inventory, retention, and what the operator can still observe |
| [docs/api.md](docs/api.md) | API reference, and the two behaviours that will surprise a client |
| [docs/performance.md](docs/performance.md) | Measured latency, throughput, and failure behaviour |
| [docs/observability.md](docs/observability.md) | Logs, metrics, tracing, dashboards, and three leaks that were caught |
| [docs/threat-model.md](docs/threat-model.md) | Actors, trust boundaries, controls, and residual risk |
| [docs/security-findings.md](docs/security-findings.md) | Every security finding, open and fixed, with evidence |
| [docs/adr/](docs/adr/) | Architecture decision records, including what was rejected and why |
| [docs/security-findings.md](docs/security-findings.md) | Running register of security findings, open and fixed |

### Decision records

- [ADR-0001](docs/adr/0001-use-searxng-as-the-search-core.md) — use SearXNG, do not modify it
- [ADR-0002](docs/adr/0002-no-relational-database.md) — ship no relational database, and what would reverse that
- [ADR-0003](docs/adr/0003-privacy-preserving-rate-limiting.md) — rate limit by rotating-salt HMAC of the client IP
- [ADR-0004](docs/adr/0004-network-isolation-and-searxng-limiter-off.md) — deny the API internet access; disable SearXNG's own limiter
- [ADR-0005](docs/adr/0005-caddy-serves-the-spa.md) — Caddy as the single edge container
- [ADR-0006](docs/adr/0006-one-circuit-breaker-not-per-engine.md) — one circuit breaker, not per-engine

## Honesty policy

This project does not claim to be "100% anonymous", and
[docs/privacy.md §9](docs/privacy.md) states exactly what the operator of the machine can
still technically observe. Performance figures are published only when measured, with the
hardware they were measured on; anything theoretical is labelled as such.

## Technology

Python 3.13 · FastAPI · React · TypeScript · Vite · SearXNG · Valkey · Caddy · Docker
Compose · Prometheus · Grafana · OpenTelemetry · pytest · Playwright · GitHub Actions

## Licence

To be selected before publication. Note that SearXNG is AGPL-3.0-or-later; Veilix
runs it as an unmodified upstream container image and does not link to or modify its code.
