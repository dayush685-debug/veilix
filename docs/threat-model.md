# Threat Model

> Written against the implementation, not against an idealised version of it.
> Where a control is partial, this document says so and names the gap. Every
> "verified" claim below corresponds to a check in `scripts/verify-stack.sh`,
> `scripts/security-scan.sh`, or a test in `apps/api/tests/security/`.

## 1. What is being protected

In priority order, because the order changes what you build:

1. **The link between a person and their queries.** This is the product. Losing
   it is worse than an outage, because an outage is recoverable and a disclosure
   is not.
2. **The instance's ability to keep working.** A self-hosted meta-search
   instance that gets abused into an upstream ban is dead even though nothing
   was breached.
3. **The host it runs on.** Standard: no code execution, no lateral movement.
4. **Operator credentials.** Admin access and API keys.

Note what is *not* on the list: user accounts, payment data, and stored personal
records. They cannot be stolen because they do not exist. That is a deliberate
architectural property, not an oversight — see [ADR-0002](adr/0002-no-relational-database.md).

## 2. Trust boundaries

```
      internet
         │  ← boundary 1: anyone can send anything here
    ┌────▼─────┐
    │  Caddy   │  the only container publishing a port
    └────┬─────┘
         │  ← boundary 2: only Caddy reaches the API
    ┌────▼─────┐
    │   API    │  NO INTERNET ROUTE (ADR-0004)
    └────┬─────┘
         │  ← boundary 3: only the API reaches SearXNG
  ┌──────▼───────┐
  │   SearXNG    │  the only container with egress
  └──────┬───────┘
         │  ← boundary 4: upstream engines are untrusted
   272 search engines — their responses are attacker-influenceable
```

**Boundary 4 is the one most systems get wrong.** Search results arrive over a
trusted channel from an untrusted author: anyone who can rank for a query
chooses the bytes in `url`, `title`, `content`, and `img_src`. They are input,
not data.

## 3. Actors

| Actor | Capability | Motivation |
|---|---|---|
| Anonymous user | Send arbitrary HTTP to the edge | Search; or abuse |
| Scraper | High-volume automated queries | Free search API |
| Hostile site owner | Rank for a query, control result fields | XSS, SSRF, drive-by |
| Network observer | See TLS metadata, timing, sizes | Deanonymisation |
| Malicious upstream engine | Return arbitrary JSON | Poison results, exploit the parser |
| Compromised dependency | Run code in a container | Anything |
| Host operator | Root on the machine | Out of scope — see §8 |

## 4. Threats, controls, and residual risk

### T1 — Rate limiter evasion via forged `X-Forwarded-For`

**Severity: High. Status: mitigated and tested.**

The limiter buckets anonymous clients by IP. `X-Forwarded-For` is an ordinary
header any client can set, so honouring it unconditionally lets an attacker pick
a fresh bucket per request. The limiter keeps running and keeps emitting healthy
metrics while limiting nothing — the failure is invisible from outside.

Two independent things had to be right:

- **Caddy replaces the header** rather than appending. Its default is to
  *append*, and the API reads the first entry. `header_up X-Forwarded-For
  {remote_host}` overwrites it. Caddy warns this directive is "unnecessary";
  the warning is wrong and the Caddyfile records why (SF-007).
- **The API trusts the header only in production**, where a proxy is known to be
  in front. In development it uses the peer address.

**Verified**: ten requests through the proxy, each with a different forged
header, against a limit of five, produced `200 ×5` then `429 ×5`.

**Residual**: clients sharing an egress IP (carrier-grade NAT, a corporate
proxy) share a bucket. Inherent to IP-based limiting.

### T2 — Stored XSS through search results

**Severity: High. Status: mitigated at three layers.**

A hostile page that ranks for a query controls its own title, snippet, and URL.
A `javascript:` URL in `url` becomes a clickable link; HTML in `title` reaches
the DOM.

1. **API** — `core/urls.py` allowlists `http`/`https`; results failing it are
   dropped, not sanitised. Sanitising is a losing arms race; dropping is not.
2. **Frontend** — all result text renders as React children, which escapes.
   There is no `dangerouslySetInnerHTML` anywhere in the codebase.
3. **CSP** — `script-src 'self'` plus one hash for the inline theme snippet, so
   an injected inline script is refused even if it reached the DOM.

**Verified**: unit tests over `javascript:`, `data:`, `vbscript:`, `file:`, and
`about:`; component tests feed `<img src=x onerror>` and `<script>` payloads
through a result card and assert they appear as text with no corresponding DOM
node.

### T3 — SSRF through the image proxy

**Severity: Medium. Status: mitigated, with a named residual gap.**

The API signs image URLs so SearXNG will proxy them, which makes it a **signing
oracle for URLs chosen by whoever ranks in results**. SearXNG has egress.

Measured blast radius, by probing from inside the container rather than
reasoning about it: SearXNG could reach `valkey:6379`, `api:8000`, and an
unrelated project's PostgreSQL at `172.20.0.2:5432` on the same Docker host.

Controls in `is_safe_to_proxy`:

- `http`/`https` only.
- Reject IP literals in private, loopback, link-local, reserved, multicast, or
  unspecified ranges.
- **Reject single-label hostnames.** This closed a live gap: Docker's embedded
  DNS resolves `valkey` and `api`, and an IP-literal check waves hostnames
  through. Every routable public name has a dot; internal service names do not.
- Reject known internal suffixes (`.local`, `.internal`, `.lan`, …).

Upstream also limits impact: responses must have an `image/` content type and
are capped at 5 MB, so bulk exfiltration is blocked.

**Residual, stated plainly**: a *public* hostname whose DNS record points at a
private address still passes. Resolving to check is impossible here — the API
container has no external DNS by design, so the same isolation that contains an
attacker also stops this function looking a name up. Closing it needs an egress
policy on the fetching side. Tracked as SF-003.

### T4 — Post-exploitation after code execution in the API

**Severity: High impact, low likelihood. Status: contained by design.**

If a dependency vulnerability yields code execution in the API process, the
attacker finds:

- **No internet route.** `backend` is `internal: true`, so no C2 callback, no
  exfiltration over the network, no cloud metadata endpoint.
- **A read-only root filesystem**, so no payload can be written to disk.
- **`/tmp` mounted `noexec,nosuid`**, so nothing can be staged and run there.
- **No package manager.** pip and setuptools are deleted from the runtime image,
  so `pip install` is not available.
- **uid 10001, `cap_drop: ALL`, `no-new-privileges`.**
- **No database** holding user data to steal, because there is none.

This turns a large class of "RCE means total loss" outcomes into "RCE is
contained". **Verified**: `verify-stack.sh` asserts the network isolation and
the read-only filesystems; a write to `/` fails in all four containers.

**Residual**: the process still sees live queries in memory while handling them.
Nothing at the application layer prevents that.

### T5 — Abuse: scraping and denial of service

**Severity: Medium. Status: mitigated.**

An unlimited public instance is scraped into an upstream ban within days — the
instance dies without anything being breached.

- Sliding-window rate limiting, 60/min anonymous and 600/min with an API key.
- Query length capped at 512 characters; page capped at 10.
- Unknown query parameters rejected (422).
- Per-request timeouts and a circuit breaker that sheds load rather than queuing
  against a wedged backend.
- Health, readiness, and metrics exempted from limiting, so an orchestrator
  cannot read a `429` as "unhealthy" and restart a service behaving correctly
  under load.

**Residual**: a distributed botnet with many source addresses defeats per-IP
limiting. That needs upstream network-level defence, which is outside this
application.

### T6 — Credential attacks

**Severity: Medium. Status: mitigated.**

- **API keys** are 256-bit random values stored as SHA-256 digests, compared in
  constant time. Deliberately *not* Argon2: slow hashing defends low-entropy
  secrets, and a random 256-bit key has no dictionary. Argon2 here would add
  ~50 ms of CPU to every authenticated request — a denial-of-service lever an
  attacker pulls for free by sending garbage keys.
- **The admin password** *is* human-chosen and low-entropy, so it uses Argon2id.
- **Username mismatch still runs the password verification**, so response timing
  does not reveal which half was wrong.
- Admin endpoints are rate-limited like everything else, so brute force is
  bounded.
- Unconfigured admin credentials **fail closed**, not open.

**Verified**: `tests/security/test_security_controls.py` covers missing, wrong,
and unconfigured credentials.

### T7 — Exposing SearXNG directly

**Severity: High if it happens. Status: guarded.**

SearXNG runs with JSON output enabled and its own bot limiter *off*, which is
correct only while it is unreachable from outside (ADR-0004). Publish its port
and it becomes an open, unauthenticated, abusable search API.

- No published ports on any service but Caddy.
- Caddy exposes exactly one SearXNG path, `/image_proxy`, and nothing else.
- **Verified two ways**: `verify-stack.sh` asserts the compose topology
  publishes ports only for the edge, and separately fetches `/search`,
  `/config`, and `/stats` through the edge and checks the *response body* — the
  SPA fallback also answers 200, so status alone would give a false pass.

### T8 — Supply chain

**Severity: Medium. Status: monitored, with one accepted exception.**

- Pinned image digests and lockfiles; `npm ci`, not `npm install`.
- `pip-audit`, `npm audit`, and Trivy in `scripts/security-scan.sh`.
- The gate is **fixable** findings, not all findings. A build that cannot be
  made green by any available action is a gate everyone learns to bypass.

Measured, before and after hardening:

| Image | Before | After | Remaining |
|---|---|---|---|
| `veilix-api` | 57 HIGH/CRITICAL | **16** | all with no published fix |
| `veilix-web` | 21 HIGH/CRITICAL | **14** | all in the Caddy binary |

The API reduction came from applying base-image updates and deleting pip and
setuptools from the runtime image — both Python findings were in packaging
tooling the application never imports.

The web image's remainder is Go modules statically linked into the upstream
Caddy binary. Verified that the newest release (v2.11.4) is built against Go
1.26.3 and every finding needs 1.26.4+. Recorded in `.trivyignore` **with an
expiry date**, because an exception without one is a permanent blind spot.

### T9 — Privacy leakage through logs and metrics

**Severity: High for the product. Status: enforced structurally.**

Logging a query is a privacy incident even though it is not a breach. Discipline
does not survive a growing codebase, so this is enforced by machinery:

- A structlog processor drops sensitive keys on **every** event, so a developer
  who logs a query sees `<redacted>` rather than shipping it.
- Route *templates* in metrics, never resolved URLs.
- 500 responses carry no exception detail; an exception raised mid-search can
  hold the query in its message.
- Caddy's access log is filtered to protocol, method, duration, size, status.

Two real leaks were found and fixed here, both by reading output rather than
configuration:

- **SF-008**: Caddy 2 renamed the access-log field from `remote_ip` to
  `client_ip`, so a filter that deleted only the old name logged every visitor's
  address while looking correct.
- Metric labels are now checked against an **allowlist**, not a denylist. A
  denylist catches only the leaks someone already imagined.

**Verified**: `verify-stack.sh` greps recent access logs for anything IP-shaped
and validates every metric label name against the allowlist.

## 5. Attacks considered and judged not worth defending against here

Being explicit about these is part of an honest model.

- **Timing side channel on the shared cache.** Real, documented in
  `privacy.md` §7, and not fixable while keeping a shared cache. Bounded by a
  short TTL; the cache can be disabled entirely.
- **Traffic analysis.** A network observer sees TLS record sizes and timing. No
  application-layer control addresses this; Tor does.
- **Malicious upstream engine returning a huge or malformed response.** Bounded
  by timeouts, upstream's 5 MB proxy cap, and strict typed parsing that drops
  unusable results.
- **CSRF.** No cookies, no sessions, no state-changing endpoints for anonymous
  users. There is nothing to forge a request against.

## 6. What would change this model

- **Adding user accounts** would introduce session management, CSRF, account
  takeover, and a database of personal data — reversing §1 entirely.
- **Multiple API replicas** would break the in-process circuit breaker and
  require sharing the rate-limit salt, or each replica enforces its own limit.
- **Any feature that fetches a user-supplied URL** would create the SSRF surface
  the API currently does not have.

## 7. Open items

| ID | Summary | Severity | Status |
|---|---|---|---|
| SF-003 | Image-proxy SSRF: public hostname resolving privately | Medium | Partially mitigated; needs egress policy |
| SF-009 | Caddy binary Go CVEs unpatchable from here | Medium | Accepted, expires 2026-11-30 |

Everything else in [`security-findings.md`](security-findings.md) is fixed.

## 8. Out of scope

**The host operator.** Someone with root on the machine can attach a debugger,
capture memory, packet-capture the internal network, or modify the code to log
queries. No application-level design prevents this, and claiming otherwise would
be dishonest.

This is the reason the project is built to be self-hosted: the way to stop
trusting the operator is to become the operator.
