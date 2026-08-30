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
- **Status**: OPEN — scheduled for Phase 5

`searxng/searxng:2026.8.29-d226b78bc` runs Granian as uid 0. The entrypoint `chown`s
config to the `searxng` user (uid 977) and then `exec`s the server directly, with no
`su-exec`, `gosu`, or `setpriv` step. Confirmed by `docker run --rm --entrypoint sh ... id`
returning `uid=0(root)`.

Root inside a container is not root on the host, but it removes a layer: combined with a
kernel or runtime vulnerability it materially shortens the path to escape.

**Planned fix**: run the service with `user: "977:977"` and verify the entrypoint's
`chown` step tolerates it. Note that ADR-0004's network isolation limits blast radius but
does not substitute for this — network policy constrains a compromised process, not a
compromised kernel.

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
link-local. A result carrying such an `img_src` now returns with no media rather than a
signed URL.

**The limit of that check, stated rather than implied.** It inspects the host as written.
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
- **Status**: ACCEPTED with a guard rail — test to be added in Phase 5

SearXNG runs with its bot-detection limiter disabled and its JSON API enabled, which is
correct for the current topology and reasoned through in ADR-0004. If SearXNG is ever
given a published port, that same configuration becomes an open, unauthenticated,
abusable JSON search API.

This is a procedural dependency, and procedure is a weak control on its own.

**Guard rail (Phase 5)**: an automated test asserting that the SearXNG service declares
no published ports in `docker-compose.yml`, so breaking the precondition fails CI rather
than shipping.

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
(`setcap -r /usr/bin/caddy`) rather than granting it back. Caddy listens on 8080 inside
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
instead of silently reinstating the leak.
