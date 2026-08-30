# ADR-0002: Ship no relational database

- **Status**: Accepted
- **Date**: 2026-08-30

## Context

Production systems usually have a relational database, and a portfolio project is
tempted to add PostgreSQL because its absence might read as a gap. The correct question
is what workload it would serve.

Enumerating the persistent state this system actually has:

1. **API key material** — a small set of operator-issued keys, stored as hashes.
2. **Admin credentials** — one hash.
3. **Cached search results** — deliberately ephemeral, already in Valkey with a TTL.
4. **Rate-limit counters** — deliberately ephemeral, already in Valkey with a TTL.
5. **Search history** — the product exists specifically in order not to have this.
6. **Metrics** — time series, which belong in Prometheus, not in a relational table.

Items 3 through 6 are either already served by a better-suited store or are things we
refuse to keep. That leaves items 1 and 2: roughly two rows, changing at the rate an
operator rotates credentials.

## Decision

**Ship no relational database.** API key hashes and the admin credential hash are
supplied through environment configuration, validated at startup, and verified with
Argon2 at request time.

## Consequences

**Positive.** No stateful service to run, back up, patch, monitor, or migrate. No
connection pool, no ORM, no migration tooling, no schema drift between environments. The
deployment stays inside a memory budget that fits a free-tier VPS. Fewer moving parts is
a reliability property, not a shortcut.

**Negative.** Rotating an API key requires a configuration change and a restart rather
than an `UPDATE`. There is no audit trail of key usage beyond aggregate metrics, and no
self-service key issuance. For the current scope — a handful of operator-issued keys —
these are acceptable.

**The trigger that reverses this decision**: self-service API key issuance for
third-party developers, requiring per-tenant quotas, rotation history, and an audit
trail. At that point the workload is genuinely relational and PostgreSQL should be added.
This ADR should be superseded rather than quietly worked around.

**What this must not become.** If a future feature needs to persist anything derived from
user queries, that is a privacy decision before it is a storage decision, and it goes
through `docs/privacy.md` first.
