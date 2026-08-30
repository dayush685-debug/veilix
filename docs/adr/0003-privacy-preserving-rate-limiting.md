# ADR-0003: Rate-limit by rotating-salt HMAC of the client IP

- **Status**: Accepted
- **Date**: 2026-08-30

## Context

The service must resist abuse: scripted scraping, denial of service, and API quota
evasion. Rate limiting requires telling clients apart, and on the open internet the only
available discriminator for anonymous users is the IP address.

This collides directly with the product's central promise. An IP address is personal
data. Storing one, even in a counter key, means the system retains a record of who
connected and when — which is the kind of record the product exists to avoid.

The obvious options:

- **Store the raw IP as the counter key.** Effective, and it retains personal data in the
  datastore. Rejected.
- **Do not rate-limit.** Preserves privacy and leaves the service trivially abusable. A
  public instance without limits will be scraped into an upstream ban within days.
  Rejected.
- **Hash the IP with a fixed salt.** Better, but a fixed salt makes the mapping stable
  forever: the same IP always produces the same key, so activity remains linkable across
  days, and an attacker with the salt can confirm any guessed IP indefinitely.
  Insufficient.

## Decision

Derive the limiter key from a **keyed hash of the client IP with a salt that rotates
daily and is never persisted**:

```
key = "rl:" + HMAC-SHA256(client_ip, salt_for_today)[:16]
```

- The salt is randomly generated and held in memory only.
- The salt rotates on a daily boundary and the previous value is discarded.
- Each key carries a TTL equal to the rate-limit window, so entries expire in minutes.
- The raw IP exists only as a function argument during the request and is never written
  to Valkey, to logs, or to metric labels.

## Consequences

**Positive.** Rate limiting works normally within the limit window. After salt rotation,
the day's keys are permanently unlinkable to any IP address — by the operator, and by
anyone who obtains the Valkey data. Cross-day correlation of a client's activity is not
merely forbidden by policy, it is not computable. The system can count a client without
being able to remember them.

**Negative and worth being precise about:**

- **A forward-guess attack remains possible within the current day.** An attacker holding
  the current salt and a candidate IP can compute its key and check whether it exists.
  This requires compromising the running process's memory, at which point they can
  observe live queries anyway, so it does not meaningfully change the threat model.
- **Rotation resets counters.** A client at its limit at the rotation boundary gets a
  fresh budget. The window is minutes and the boundary is daily, so the exploitable gain
  is one extra window per day. Accepted.
- **In a multi-replica deployment the salt must be shared**, or replicas will compute
  different keys for the same client and each will enforce its own limit. Deriving the
  salt from a shared secret plus the current date is the intended fix; it is not needed
  at single-replica scale and is not implemented yet.
- **Shared egress IPs share a bucket.** Users behind carrier-grade NAT or a corporate
  proxy are limited collectively. This is inherent to IP-based limiting, not to the
  hashing.

**Trust dependency.** Because the client IP arrives via `X-Forwarded-For` from Caddy, the
limiter is only sound if that header is trusted from the proxy alone and rejected from
elsewhere. Getting this wrong turns the limiter into a header the attacker controls.
Enforced in the Caddy configuration and verified by a security test in Phase 5.
