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
- **Status**: OPEN — mitigation scheduled for Phase 5

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

**Planned mitigation (Phase 5)**, in order of value:

1. Validate before signing. Only sign `http`/`https` URLs, and reject hosts that are
   literal IP addresses in private, loopback, link-local, or unique-local ranges.
2. Note the limit of that check honestly: the API container has no external DNS, so it
   cannot resolve a hostname to confirm it points somewhere public. A hostile
   *hostname* resolving to an internal address defeats step 1. DNS-rebinding-style
   attacks are not addressed by input validation alone.
3. Consider constraining SearXNG's outbound network for the `image_proxy` network
   context specifically, which is where an egress policy would actually bite.
4. Cap proxied-image concurrency so the surface cannot be used as an amplifier.

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
- **Status**: OPEN — Phases 3 and 4

Every field in a search result — `url`, `title`, `content`, `img_src`, `iframe_src` — is
authored by a third party and reaches the user's browser through us. Anyone able to rank
for a query can choose those bytes.

Concrete risks: `javascript:` or `data:` URLs in the `url` field becoming clickable links;
HTML in `title` or `content` reaching the DOM unescaped; `iframe_src` values causing
embedded third-party frames.

**Planned handling**:

- API layer (Phase 3): allowlist URL schemes to `http` and `https` on every URL-bearing
  field, and drop results that fail.
- Frontend (Phase 4): render all result text as text nodes, never as HTML; no
  `dangerouslySetInnerHTML` anywhere in the result path.
- Caddy (Phase 5): a Content-Security-Policy that would contain a failure at either layer.

Three independent layers, because this is the most likely place for a mistake to reach a
real user.
