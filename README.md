# Veilix

A privacy-first, self-hostable meta-search platform built around
[SearXNG](https://github.com/searxng/searxng).

It aggregates results from many search engines without accounts, without cookies, without
search history, and without building a profile of the person asking.

> **Build status: Phase 3 of 10 complete.** SearXNG, Valkey, and the FastAPI backend
> run as four hardened containers. 151 tests pass, mypy runs strict and clean, and
> `scripts/verify-stack.sh` asserts 18 architectural claims against the running stack.
> The frontend lands in Phase 4. This README is filled out as each phase completes, and
> no capability is described here before it exists.

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
Browser → Caddy (TLS, headers, serves SPA) → FastAPI (validate, limit, orchestrate)
                                                 ├→ Valkey  (cache, rate-limit counters)
                                                 └→ SearXNG (→ 272 upstream engines)
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
