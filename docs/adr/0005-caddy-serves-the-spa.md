# ADR-0005: Caddy as reverse proxy, serving the SPA directly

- **Status**: Accepted
- **Date**: 2026-08-30

## Context

The system needs TLS termination, HTTP-to-HTTPS redirection, security headers,
compression, and routing between a static frontend and an API. It also needs to serve the
built React application.

The conventional arrangement is a reverse proxy container plus a separate nginx container
serving static files. On a host whose Docker VM has 2.77 GiB of RAM shared with unrelated
workloads, every container has to justify itself.

Frontend framework choice interacts with this. Next.js would require a running Node
process in production — and that process would see every search query in plaintext, which
is a privacy regression, not merely extra weight. A static SPA has no server-side
component to leak through.

## Decision

Use **Caddy** as the single edge container, and **build the SPA into the Caddy image** at
image build time. Caddy serves the static assets with `file_server` and reverse-proxies
`/api/*` to the FastAPI service.

The frontend is **React + TypeScript + Vite**, producing static output with no runtime
server.

## Consequences

**Positive.** Automatic ACME certificate issuance and renewal with no certbot cron job,
no renewal hook, and no expiry incident — the single largest source of self-hosted TLS
outages simply does not apply. HTTP/3 and compression are defaults. The Caddyfile is
short enough to review in one screen, which matters because it holds the security
headers and the `X-Forwarded-For` trust rule that the privacy-preserving rate limiter
depends on (ADR-0003).

One fewer container, and no ambiguity about which layer owns static assets. SPA and proxy
config ship as one versioned artefact, so they cannot drift apart between environments.

**Negative.** A frontend-only change requires rebuilding the Caddy image rather than
redeploying a static bundle independently. With CI building images this is a non-issue at
this scale, but it does couple two deploy cadences that could otherwise be separate.

Caddy's automatic HTTPS needs a public DNS name and reachable ports 80 and 443. Local
development therefore runs over plain HTTP on localhost, which means the TLS path is not
exercised in day-to-day development and must be validated separately before a production
deploy.

**Rejected alternatives.** Nginx — more configuration surface and a manual certbot
lifecycle to own. Traefik — excellent for dynamic, label-driven service discovery, but
this topology is five fixed services; the indirection would cost readability and buy
nothing. Next.js — an SSR server that would observe every query, which the product cannot
accept.
