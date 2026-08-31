# Security Findings

A running register of security issues found during the build, carried forward until
resolved. Findings are recorded when discovered, not when convenient. The full threat
model is produced in Phase 5; this file is the evidence trail feeding it.

Status values: **OPEN** · **MITIGATED** · **ACCEPTED** (documented residual risk) ·
**FIXED**

---

## SF-001 — SearXNG container runs as root

- **Found**: Phase 0, image inspection
- **Severity**: Medium
- **Status**: FIXED in Phase 5

`searxng/searxng:2026.8.29-d226b78bc` runs Granian as uid 0. The entrypoint `chown`s
config to the `searxng` user (uid 977) and then `exec`s the server directly, with no
`su-exec`, `gosu`, or `setpriv` step. Confirmed by `docker run --rm --entrypoint sh ... id`
returning `uid=0(root)`.

Root inside a container is not root on the host, but it removes a layer: combined with a
kernel or runtime vulnerability it materially shortens the path to escape.

**Fix applied**: `user: "977:977"` in compose. This is safe because the entrypoint guards
its `chown` with an `id -u = 0` test, so as a non-root user that step is skipped with a
warning rather than failing. Verified: `docker exec veilix-searxng id` returns
`uid=977(searxng)`, the container is healthy, and search returns results.

Hardened further in the same pass: `read_only: true` with `/tmp` and
`/var/cache/searxng` as `noexec,nosuid` tmpfs mounts. SearXNG writes SQLite caches to
both, so they are now memory-only and never reach disk.

**A privacy question that had to be answered first**: those SQLite files might have
contained query text, which would contradict `docs/privacy.md`. Checked instead of
assumed — a canary query was run and both databases were searched for its text. They hold
engine metadata and tracker patterns, no queries. The tmpfs is belt-and-braces on top.

Note that ADR-0004's network isolation limits blast radius but does not substitute for
this — network policy constrains a compromised process, not a compromised kernel.

---

## SF-002 — `cap_drop: ALL` broke Valkey's privilege drop

- **Found**: Phase 2, first stack bring-up
- **Severity**: Low (availability, not confidentiality)
- **Status**: FIXED in Phase 2

Adding `cap_drop: ALL` put the Valkey container into a crash loop with
`setpriv: setresuid failed: Operation not permitted`. Its entrypoint starts as root and
uses `setpriv` to drop to the `valkey` user, which needs `CAP_SETUID` and `CAP_SETGID` —
exactly what the blanket drop removed.

**Fix applied**: `user: "999:1000"` in compose, so the process starts as the unprivileged
user and never needs to drop. Strictly better than granting the capabilities back — there
is no privileged window at all. Verified: `docker exec veilix-valkey id` returns
`uid=999(valkey)`.

**Lesson worth keeping**: blanket capability drops surface as startup crashes, which is
the good failure mode. The instinct to hand the capability back is usually wrong; the
better question is why the process was root to begin with.

---

## SF-003 — Image proxy is an SSRF surface reachable via search results

- **Found**: Phase 2, verifying the image-proxy privacy claim
- **Severity**: Medium
- **Status**: PARTIALLY MITIGATED — signing guard done in Phase 3, egress policy pending

SearXNG's `/image_proxy` fetches any URL accompanied by a valid
`HMAC-SHA256(secret_key, url)` signature. Because the Veilix API holds that secret in
order to rewrite `img_src` values (see `docs/privacy.md` §6), **the API is a signing
oracle for whatever URLs appear in search results**.

A hostile page that ranks in results could carry an `img_src` pointing at an internal
address — `http://169.254.169.254/latest/meta-data/`, or a service on the container
network. The API would sign it, and SearXNG — which does have internet egress — would
fetch it.

Upstream constrains the impact: responses must carry an `image/` or
`binary/octet-stream` content type, and the body is capped at 5 MB, so straightforward
exfiltration of JSON metadata is blocked. What remains is **blind SSRF**: response codes
and timing still leak whether an internal host and port are live.

**Done — Phase 3.** `core/urls.is_safe_to_proxy` validates before signing: only
`http`/`https` schemes, and any host that is a literal IP address in a private, loopback,
link-local, reserved, multicast, or unspecified range is refused rather than signed. Unit
tests cover the cloud metadata address, all three RFC 1918 ranges, IPv6 loopback, and IPv6
link-local. A result carrying such an `img_src` now returns with no media instead of a
signed URL.

**The limit of that check, stated, not implied.** It inspects the host as written.
The API container has no external DNS — the same isolation that contains an attacker
(ADR-0004) also prevents this function from resolving a name — so a hostile *hostname*
that resolves to an internal address still passes. Input validation alone does not address
DNS-rebinding-style attacks, and this layer should not be described as if it did.

**Remaining — Phase 5:**

1. Constrain SearXNG's outbound network for the `image_proxy` network context
   specifically, which is where an egress policy would actually bite. This is the step
   that closes the hostname gap; the Phase 3 work narrows the surface but does not close
   it.
2. Cap proxied-image concurrency so the surface cannot be used as a request amplifier.

**Why this is written down rather than quietly fixed later**: the privacy documentation
makes a claim about image proxying protecting users, and this is the cost of that
protection. A reader deserves both halves.

---

## SF-004 — `limiter: false` is safe only while SearXNG stays unpublished

- **Found**: Phase 1, design
- **Severity**: High **if** the precondition is ever broken
- **Status**: MITIGATED — automated guard rails added in Phase 5

SearXNG runs with its bot-detection limiter disabled and its JSON API enabled, which is
correct for the current topology and reasoned through in ADR-0004. If SearXNG is ever
given a published port, that same configuration becomes an open, unauthenticated,
abusable JSON search API.

This is a procedural dependency, and procedure is a weak control on its own.

**Guard rails added (Phase 5)**, two of them, because one was not enough:

1. `verify-stack.sh` asserts the production compose topology publishes host ports for the
   edge service *only*. An earlier version simply counted all published ports and started
   failing the moment Caddy legitimately published one — a check too blunt to survive
   contact with the architecture it guards.
2. It separately fetches `/search`, `/config`, and `/stats` **through the edge** and
   inspects the response *body*. Status alone gives a false pass here, because the SPA
   fallback also answers 200; only the body distinguishes the app shell from leaked
   SearXNG output.

**Note on the dev override**: `docker-compose.dev.yml` does publish SearXNG, deliberately
bound to `127.0.0.1` so it is not reachable from the local network. The loopback prefix is
load-bearing — without it Docker binds `0.0.0.0` and commonly bypasses the host firewall.
The planned test must account for this file being development-only.

---

## SF-005 — Search-result URLs are attacker-influenced content

- **Found**: Phase 2, reviewing the JSON result schema
- **Severity**: Medium
- **Status**: MITIGATED — all three layers in place as of Phase 4

Every field in a search result — `url`, `title`, `content`, `img_src`, `iframe_src` — is
authored by a third party and reaches the user's browser through us. Anyone able to rank
for a query can choose those bytes.

Concrete risks: `javascript:` or `data:` URLs in the `url` field becoming clickable links;
HTML in `title` or `content` reaching the DOM unescaped; `iframe_src` values causing
embedded third-party frames.

**Handling**:

- **API layer (Phase 3) — DONE.** `core/urls.py` allowlists `http` and `https` on every
  URL-bearing field, and `providers/searxng.py` drops any result that fails. Covered by
  unit tests over `javascript:`, `data:`, `vbscript:`, `file:`, and `about:` URLs, and by
  an HTTP-level test asserting such results never reach a client.
- **Frontend (Phase 4) — DONE.** All result text renders as React children, which escapes
  it. There is no `dangerouslySetInnerHTML` anywhere in the codebase. Component tests feed
  `<img src=x onerror=...>` and `<script>` payloads through a result card and assert they
  appear as visible text with no corresponding DOM node.
- **Caddy (Phase 4) — DONE.** A Content-Security-Policy with `script-src 'self'` plus a
  single SHA-256 hash for the inline theme snippet, `object-src 'none'`, and
  `base-uri 'none'`. An injected inline script is refused even if it reached the DOM.

Three independent layers, because this is the most likely place for a mistake to reach a
real user.

---

## SF-006 — `cap_drop: ALL` broke Caddy's exec via file capabilities

- **Found**: Phase 4, first edge bring-up
- **Severity**: Low (availability, not confidentiality)
- **Status**: FIXED in Phase 4

The Caddy container crash-looped with `exec /usr/bin/caddy: operation not permitted`. The
image ships the binary with `cap_net_bind_service` as a *file capability* so it can bind
port 80 unprivileged, and the kernel refuses to exec a binary whose permitted capability
set is not within the container's bounding set — which `cap_drop: ALL` empties.

Same root cause family as SF-002, different mechanism: that one was `setpriv` needing
`CAP_SETUID` at runtime, this one is file capabilities checked at exec time.

**Fix applied**: strip the capability from the binary at build time
(`setcap -r /usr/bin/caddy`) instead of granting it back. Caddy listens on 8080 inside
the container and Docker publishes the privileged port to it, so the capability was never
needed — and removing it means this binary cannot bind a privileged port at all, even if
something later tries.

---

## SF-007 — Caddy appends to `X-Forwarded-For`; the API reads the first entry

- **Found**: Phase 4, reviewing the proxy configuration
- **Severity**: **High** if misconfigured — the rate limiter would be fully evadable
- **Status**: FIXED and covered by an end-to-end test

`core/security.client_ip_from_headers` takes the **first** entry of `X-Forwarded-For`,
which is correct for a proxy that *replaces* the header. Caddy's `reverse_proxy` default
is to **append** the client address to whatever the client already sent.

Under the default, an attacker sending `X-Forwarded-For: 1.2.3.4` would have Caddy
forward `1.2.3.4, <real address>`, the API would bucket them as `1.2.3.4`, and rotating
that value per request would give every request its own rate-limit bucket. The limiter
would keep running, keep emitting metrics, and limit nothing — the failure is completely
invisible from the outside.

**Fix**: `header_up X-Forwarded-For {remote_host}` in the `/api/*` handler, which *sets*
the header and discards whatever the client sent.

**Caddy warns that this directive is "unnecessary".** The warning is misleading, and
following it would reintroduce the vulnerability. The Caddyfile records this inline so
nobody removes the line to silence the log.

**Verified end to end**: ten requests through the proxy, each carrying a different forged
`X-Forwarded-For`, against a limit of five, produced `200 200 200 200 200 429 429 429 429
429`. The forged values never reached the limiter.

---

## SF-008 — Caddy access logs recorded client IP addresses

- **Found**: Phase 4, reading real log output
- **Severity**: Medium (privacy)
- **Status**: FIXED in Phase 4

The access-log filter deleted `request>remote_ip`, which is what Caddy 1 called the field.
Caddy 2 emits it as `client_ip`, so every access-log line contained the visitor's address
— directly contradicting `docs/privacy.md` §4, while the configuration looked correct.

**Fix**: delete `request>client_ip` (and `request>host`) as well. The remaining log line
carries only protocol, method, duration, size, and status.

**Worth noting how this was found**: not by reading the configuration, which looked right,
but by reading the output it produced. `scripts/verify-stack.sh` now greps recent access
logs for anything shaped like an IP address, so a future field rename fails a check
instead of quietly reinstating the leak.


---

## SF-009 — Unpatchable Go CVEs in the upstream Caddy binary

- **Found**: Phase 5, Trivy scan
- **Severity**: Medium
- **Status**: ACCEPTED, expiring 2026-11-30

Fourteen HIGH/CRITICAL findings sit in Go modules statically linked into
`usr/bin/caddy` — `golang.org/x/net`, `golang.org/x/text`, `google.golang.org/grpc`, and
the Go standard library. They are not our dependencies and cannot be patched from here.

**Verified, not assumed**: pulled the newest published image (`caddy:2-alpine`,
v2.11.4) and re-scanned. It is built against Go 1.26.3; every finding needs 1.26.4 or
later. `apk --no-cache upgrade` in the Dockerfile fixed the Alpine packages — taking the
count from 21 to 14 — but cannot touch a compiled binary.

**The escape hatch, if these become material**: build Caddy from source with xcaddy on a
newer Go toolchain. That trades a maintained upstream release for a build we own, which
is a real ongoing cost and not currently justified for a proxy that terminates TLS and
serves static files.

**Why this is dated rather than simply ignored**: Caddy is the only internet-facing
container here. An exception without an expiry is a permanent blind spot, so
`.trivyignore` carries `exp:2026-11-30` on every entry and Trivy will re-report them
after that date.

---

## SF-010 — Internal Docker hostnames bypassed the image-proxy SSRF guard

- **Found**: Phase 5, probing reachability from inside the container
- **Severity**: Medium
- **Status**: FIXED in Phase 5

`is_safe_to_proxy` rejected private IP *literals* but accepted any hostname. Inside
Docker that is not enough: the embedded DNS server resolves service names, so
`http://valkey:6379/` and `http://api:8000/` are hostnames and sailed straight through.
The API would have signed them, and SearXNG — which has egress and sits on the same
network — would have fetched them.

Measured instead of theorised. Probing from inside `veilix-searxng`:

```
REACHABLE  researchos-postgres, unrelated project (172.20.0.2:5432)
REACHABLE  veilix valkey (valkey:6379)
REACHABLE  veilix api (api:8000)
blocked    cloud metadata (169.254.169.254:80)
REACHABLE  public internet (example.com:443)
```

An unrelated project's database on the same Docker host was reachable from the container
that performs proxy fetches.

**Fix**: reject **single-label hostnames**. Every routable public name has at least one
dot; internal Docker service names, `localhost`, and short intranet names do not. That one
rule removes the entire container-DNS attack surface. Known internal suffixes
(`.local`, `.internal`, `.lan`, `.home.arpa`, …) are rejected too, for names like
`db.internal` that do carry a dot.

Covered by parametrised tests over `valkey`, `api`, `searxng`, `localhost`, `db`, and each
internal suffix, plus a test asserting ordinary public hostnames still work.

**Residual**: a public-looking name whose DNS record points at a private address still
passes, and cannot be caught here — the API container has no external DNS by design
(ADR-0004), so the isolation that contains an attacker also prevents this check from
resolving anything. Rolled into SF-003.


---

## SF-011 — OpenTelemetry traces exported search queries

- **Found**: Phase 7, canary query against a live collector
- **Severity**: **High** for the product
- **Status**: FIXED in Phase 7

OpenTelemetry's HTTP instrumentations record the full request URL by default. With
tracing enabled, every search exported:

```
http.url = http://searxng:8080/search?q=CANARYTRACE98765&format=json&categories=general
http.url = http://127.0.0.1:8088/api/v1/search?q=CANARYTRACE98765&category=general
```

to whatever trace backend the operator had configured — user queries shipped off-box, in
direct contradiction of `docs/privacy.md` §4.

This is worse than an ordinary logging mistake for two reasons. Nobody thinks of a tracing
backend as somewhere search history accumulates, and the trigger is entirely benign:
someone enables tracing to debug a latency problem and silently starts exporting what
people searched for.

**Found with a canary**, not by reading the instrumentation's source. The value of running
a real collector and grepping its output for a known string is that it tests what the
system *does* rather than what the code appears to say.

**Fix**: a span processor strips everything after `?` from `http.url` and `url.full`
before export. The path is kept — it is useful and carries no user data. Verified with a
fresh collector: the canary string appears nowhere in exported spans, and spans still
arrive with useful names and route attributes.

Pinned by `tests/unit/test_telemetry_redaction.py`, because the symptom is invisible from
inside the process — the application behaves identically whether or not the leak exists.

**Implementation note worth keeping.** The processor was first written as a duck-typed
class and broke tracing outright: the SDK calls a private `_on_ending` hook on every
processor, so a class that merely looks like a `SpanProcessor` raises inside `span.end()`.
Structural typing is not enough when the protocol has private members. It subclasses the
real base class now, defined inside the successful-import block so the SDK stays optional.

---

## SF-012 — Tracing was configured but non-functional in two ways

- **Found**: Phase 7, while verifying tracing end to end
- **Severity**: Low (observability, not security)
- **Status**: FIXED in Phase 7

Two independent defects, both of which made a documented feature silently do nothing —
the same class as the Phase 6 missing-environment-variables bug.

**The SDK was not installed.** The Dockerfile ran `pip install .` instead of
`pip install ".[otel]"`, so `setup_tracing` hit its `ImportError` branch and returned
`False`. An operator could set `VEILIX_OTLP_ENDPOINT`, restart, and get no traces and no
explanation. The extra is now installed, and the import failure logs an error naming the
fix instead of failing silently.

**Server spans were never created.** `FastAPIInstrumentor.instrument_app` was called from
lifespan, which runs after the application object is assembled. Starlette builds its
middleware stack once, so the instrumentation middleware never made it in: outgoing httpx
client spans appeared and HTTP server spans did not. Tracing looked enabled and was half
missing. Setup moved into the app factory, before the app serves anything.

**How both were caught**: by running a real OTel collector, sending a request, and
counting spans. One span arrived where six were expected.


---

## SF-013 — Caddy reported unhealthy forever in the production configuration

- **Found**: Phase 9, by starting the production compose not by reading it
- **Severity**: Medium (availability)
- **Status**: FIXED in Phase 9

The base healthcheck probes `http://127.0.0.1:8080/`, which is where Caddy listens when
`VEILIX_SITE_ADDRESS` is a bare port. In production that variable is a hostname, so Caddy
binds 80 and 443 instead and the check could never succeed.

Consequences: `depends_on: service_healthy` blocks dependent services, and an orchestrator
restarts an edge container that is serving traffic perfectly well. The site works; the
platform believes it does not.

**The obvious fix is also wrong**, which is the part worth recording. Changing the probe to
`wget --spider http://127.0.0.1:80/` fails too: wget FOLLOWS the 308 redirect into HTTPS
and then fails the TLS handshake, because the container does not trust Caddy's own internal
CA — and behind a real hostname, `127.0.0.1` matches no SNI site at all. busybox `wget` has
neither `--max-redirect` nor `--no-check-certificate`, so neither escape hatch exists.

**Fix**: assert the redirect itself, which is exactly what a healthy auto-HTTPS Caddy
produces and is provable without a trusted certificate:

```
wget -S --spider -T 5 http://127.0.0.1:80/ 2>&1 | grep -qE "HTTP/1\.[01] 30[18]"
```

Verified: all four containers healthy under `docker-compose.prod.yml`, with HTTPS
answering 200, HTTP redirecting 308, and search returning results over TLS.

---

## SF-014 — Deployment documentation named flags that do not exist

- **Found**: Phase 9, running the project's own instructions
- **Severity**: Low
- **Status**: FIXED in Phase 9

`docs/deployment.md` told operators to run `python scripts/hash_secret.py --password`. The
script's flag is `--admin-password`, and there is no `--password`, so the very first
command a new operator runs would fail with a usage error.

Trivial, and worth logging: documentation is only verified when someone executes it. The
commands in that document have now each been run.
