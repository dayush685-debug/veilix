# Security Model

> The controls that are implemented, how to configure them, and what each one
> is actually defending against. The adversarial analysis lives in
> [threat-model.md](threat-model.md); the register of everything found and
> fixed is [security-findings.md](security-findings.md).

## Posture at a glance

| | Status |
|---|---|
| Containers running as root | **0 of 6** |
| Containers with a read-only root filesystem | **6 of 6** |
| Containers with `cap_drop: ALL` + `no-new-privileges` | **6 of 6** |
| Services publishing a host port | **1** (the edge) |
| Fixable HIGH/CRITICAL vulnerabilities | **0** across both images |
| Findings logged / fixed | **14 / 12** |

The two open items are named in §9. Both are documented rather than quietly
carried.

## 1. The shape of the defence

```
internet ──▶ Caddy ──▶ API ──▶ SearXNG ──▶ upstream engines
             │         │        │
             │         │        └─ the ONLY container with egress
             │         └─ NO internet route at all
             └─ the only container publishing a port
```

The single most useful property here is that the API container **cannot reach
the internet**. `backend` is declared `internal: true`, which strips its
gateway. Code execution in the API process yields no command-and-control
callback, no exfiltration over the network, and no cloud metadata endpoint.

That is verified, not asserted: `scripts/verify-stack.sh` runs a container on
that network and confirms it cannot resolve or reach `example.com`, while
SearXNG — which also sits on `egress` — can.

## 2. Authentication

Two credential types, treated differently.

**API keys** are 256-bit random values we generate, so they carry full entropy
and are not guessable. Verified with SHA-256 and a constant-time compare, with
the loop running to completion so timing does not leak which key matched.

**The admin password** is chosen by a human, so it is low-entropy and
vulnerable to offline cracking if the hash leaks. It uses **Argon2id**.

Using Argon2 for API keys would be caution that backfires: it adds ~50 ms of
deliberate CPU burn to every authenticated request, which is a denial-of-service
lever an attacker pulls for free by sending garbage keys. Slow hashing defends
secrets that have a dictionary. A random 256-bit key does not have one.

Admin verification runs the password check **even when the username is wrong**,
so response timing cannot be used to enumerate the valid username first.

**Unconfigured credentials fail closed.** An instance that forgot to set an
admin hash gets a locked door, not a lobby.

```bash
python scripts/hash_secret.py --admin-password   # Argon2id hash
python scripts/hash_secret.py --api-key          # key + SHA-256 digest
```

## 3. Rate limiting, without storing who you are

Abuse prevention needs to tell clients apart; anonymous clients are told apart
by IP. That is a real tension with a no-tracking product, and the resolution is
the part worth understanding.

```
key = "rl:" + HMAC-SHA256(client_ip, salt_for_today)[:16]
```

The raw address is a function argument and nothing else — never written to
Valkey, a log, or a metric label. The salt is derived from a configured seed
plus the UTC date, held in memory, and never persisted. Once it rotates,
yesterday's keys cannot be linked to any address by anyone, including whoever
holds the database.

The window slides: a fixed window would let a client spend its full budget in
the last second of one window and again in the first second of the next,
doubling real burst capacity. A Lua script does the increment-and-read
atomically, because read-then-write races precisely under the burst the limiter
exists to stop.

**It fails open.** If Valkey is unreachable, requests are permitted and the
failure is logged loudly. Failing closed would turn a cache outage into a
site-wide outage — a degraded dependency becoming a hard one.

| Setting | Default |
|---|---|
| `VEILIX_RATELIMIT_REQUESTS` | 60 / minute, anonymous |
| `VEILIX_RATELIMIT_APIKEY_REQUESTS` | 600 / minute |
| `VEILIX_RATELIMIT_WINDOW_S` | 60 |

### The part that had to be right twice

The limiter is only sound if `X-Forwarded-For` is trusted from the proxy and
nowhere else. Caddy's `reverse_proxy` **appends** to that header by default,
while the API reads the *first* entry — correct for a proxy that replaces it,
catastrophic for one that appends.

Under the default, an attacker sending `X-Forwarded-For: 1.2.3.4` would be
bucketed as `1.2.3.4`, rotate it per request, and never be limited, while the
limiter kept emitting perfectly healthy numbers. The Caddyfile sets the header
explicitly, and **Caddy warns that this is "unnecessary"** — the warning is
wrong, and the file records why so nobody deletes the line to silence a log.

Verified end to end: ten requests with different forged headers against a limit
of five produced `200 ×5` then `429 ×5` (SF-007).

## 4. Input validation

Every parameter is constrained, because unconstrained input reaching a fan-out
to dozens of upstream engines is how a search API becomes someone else's
denial-of-service tool.

- Query capped at 512 characters
- Page capped at 10
- Language matched against a pattern, not free text
- **Unknown query parameters are rejected** (`extra="forbid"`), so a typo fails
  loudly instead of being ignored without complaint
- Request bodies capped at the edge

## 5. Untrusted content from search results

Every field in a result — `url`, `title`, `content`, `img_src` — is authored by
a third party. Anyone who can rank for a query chooses those bytes. They arrive
over a trusted channel and are untrusted input.

Three independent layers, because this is the likeliest place for a mistake to
reach a real user:

1. **API** — `core/urls.py` allowlists `http`/`https`. Results failing it are
   **dropped, not sanitised**; sanitising is an arms race, dropping is not.
2. **Frontend** — all result text renders as React children, which escapes.
   There is no `dangerouslySetInnerHTML` anywhere in the codebase.
3. **CSP** — `script-src 'self'` plus one SHA-256 hash for the inline theme
   snippet, so an injected inline script is refused even if it reached the DOM.

Component tests feed `<img src=x onerror>` and `<script>` payloads through a
result card and assert they appear as visible text with no corresponding DOM
node.

## 6. SSRF

The API fetches exactly one hardcoded internal URL. No endpoint accepts a
user-supplied URL and fetches it, so the classic surface does not exist there.

The real surface is the **image proxy**. The API signs image URLs with the
shared SearXNG secret so thumbnails are fetched server-side instead of by the
user's browser — which is what stops every image host learning the viewer's IP.
That signing makes the API an oracle for URLs appearing in results.

Blast radius was measured, not reasoned about. Probing from inside the
container that performs the fetches:

```
REACHABLE  an unrelated project's PostgreSQL (172.20.0.2:5432)
REACHABLE  veilix valkey (valkey:6379)
REACHABLE  veilix api  (api:8000)
REACHABLE  the public internet
```

`is_safe_to_proxy` therefore rejects:

- non-`http`/`https` schemes
- IP literals in private, loopback, link-local, reserved, multicast, or
  unspecified ranges
- **single-label hostnames** — this closed a live gap, because Docker's
  embedded DNS resolves `valkey` and `api`, and an IP-literal check waves
  hostnames straight through. Every routable public name has a dot; internal
  service names do not (SF-010)
- known internal suffixes (`.local`, `.internal`, `.lan`, …)

Upstream limits impact further: proxied responses must carry an `image/`
content type and are capped at 5 MB.

**Residual, stated plainly**: a public-looking hostname whose DNS record points
at a private address still passes. Resolving to check is impossible here — the
API container has no external DNS by design, so the isolation that contains an
attacker also stops this check looking a name up. Closing it needs an egress
policy on the fetching side (SF-003).

## 7. Edge and transport

Automatic TLS via Caddy — no certbot, no renewal cron, no expiry incident,
because the single largest source of self-hosted TLS outages simply does not
apply.

Headers applied to every response:

| Header | Value |
|---|---|
| `Content-Security-Policy` | `default-src 'self'`; `script-src 'self' 'sha256-…'`; `img-src 'self' data:`; `object-src 'none'`; `base-uri 'none'`; `frame-ancestors 'none'` |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` |
| `Referrer-Policy` | `no-referrer` |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Permissions-Policy` | sensors and APIs the app never uses, all denied |
| `Server` | **removed** |

`img-src 'self' data:` forbids every third-party image host outright. That is
only possible *because* thumbnails are proxied — so if proxying ever broke, the
CSP would visibly break the page rather than quietly leak. The privacy
decision and the security control reinforce each other.

`scripts/check-csp-hash.sh` fails CI when the inline theme snippet and its hash
drift apart, because the symptom is otherwise a white flash reproducible only
in production.

## 8. Supply chain

`scripts/security-scan.sh` runs `pip-audit`, `npm audit`, and Trivy.

**The gate is *fixable* findings, not all findings.** A build that no available
action can make green is a gate everybody learns to bypass. Accepted findings
live in `.trivyignore`, each with a reason and an **expiry date**, because an
exception without one is a permanent blind spot.

Measured, before and after hardening:

| Image | Before | After |
|---|---|---|
| `veilix-api` | 57 HIGH/CRITICAL | **16**, none with a published fix |
| `veilix-web` | 21 HIGH/CRITICAL | **14**, all in the upstream Caddy binary |

The API reduction came from applying base-image updates and **deleting pip and
setuptools from the runtime image** — both Python findings lived in packaging
tooling the application never imports, and removing it also means an attacker
with code execution cannot `pip install` a payload.

CI additionally verifies against the built image that it runs as uid 10001,
ships no package manager, is not the build stub, and carries the OTel SDK —
because a base-image change can silently reset any of those.

## 9. Open items

| ID | Summary | Status |
|---|---|---|
| SF-003 | A public hostname resolving to a private address still passes the proxy guard | Partially mitigated; needs an egress policy, which input validation cannot provide |
| SF-009 | Go CVEs compiled into the upstream Caddy binary | Accepted, `.trivyignore`, expires 2026-11-30 |

Everything else in the register is fixed.

## 10. Reporting a vulnerability

Open a private security advisory on the repository instead of a public issue.
Please include the request ID from any error response — it maps to a
server-side log line, and that log line contains no query text or client
address, so it is safe to share.

## 11. Out of scope

**The host operator.** Someone with root can attach a debugger, capture memory,
packet-capture the internal network, or modify the code to log queries. No
application-level design prevents this, and claiming otherwise would be
dishonest.

This is the reason the project is built to be self-hosted: the way to stop
trusting the operator is to become one.
