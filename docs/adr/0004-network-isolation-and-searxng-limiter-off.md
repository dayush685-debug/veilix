# ADR-0004: Isolate the API container from the internet, and disable SearXNG's limiter

- **Status**: Accepted
- **Date**: 2026-08-30

## Context

Two decisions that look unrelated are in fact the same decision, because both follow from
where the trust boundary sits.

**First**, the API container's outbound needs are narrow. It talks to SearXNG and to
Valkey. It has no reason to reach the internet: it fetches no user-supplied URLs, calls
no third-party services, and downloads nothing at runtime.

**Second**, SearXNG ships a bot-detection limiter that identifies clients by IP, reading
`X-Forwarded-For` from trusted proxies. In our topology, every request to SearXNG
originates from one place — the API container.

## Decision

**Three Docker networks, with the API container denied any internet route:**

| Network | Members | Property |
|---|---|---|
| `edge` | Caddy, API | Caddy is the only container publishing ports |
| `backend` | API, Valkey, SearXNG | `internal: true` — no gateway, no internet |
| `egress` | SearXNG only | default bridge; SearXNG can reach upstream engines |

The API sits on `edge` and `backend` only. Because `backend` is `internal: true` and
`edge` carries no default route outward for the API, the API container **cannot reach the
internet at all**. SearXNG is the only container with egress, because it is the only one
that needs it.

SearXNG's limiter is set to `false`. Rate limiting is enforced at the edge (Caddy)
and in the API, where genuine client identity exists.

## Consequences

**Positive.** Post-exploitation is materially harder: an attacker with code execution in
the API process cannot dial out to a command-and-control host, cannot exfiltrate over the
network, and cannot reach cloud instance-metadata endpoints. This turns a large class of
"RCE means total loss" outcomes into "RCE is contained". It also means the classic SSRF
impact is bounded even if an SSRF bug were introduced later.

Disabling SearXNG's limiter avoids a concrete misbehaviour: with all traffic arriving
from one container IP, the limiter would treat the entire user base as a single client
and would either throttle everyone at once or, with the container IP passlisted, do
nothing at all. Neither is rate limiting. Enforcing at the edge puts the control where
real client identity exists.

**Negative and load-bearing:**

- **`limiter: false` is safe only while SearXNG is unpublished.** If SearXNG's port is
  ever exposed, this becomes an open, unauthenticated, abusable JSON search API. The
  mitigation is procedural and must be treated as such: publishing SearXNG and enabling
  the limiter must happen in the same commit. A Phase 5 test asserts SearXNG publishes no
  ports.
- The API cannot fetch anything at runtime, by design. Any future feature needing
  outbound calls must either route through a service on `backend` or explicitly and
  visibly change this topology — which is the point: the change becomes a reviewable
  event rather than an accident.
- **Debugging is slightly harder** — no `curl` from inside the API container to test
  external connectivity. Acceptable.
- Compose networking is not a security boundary against a container escape. It
  constrains a compromised *process*, not a compromised *kernel*. Container hardening
  (Phase 5) addresses the other half, and neither substitutes for the other.
