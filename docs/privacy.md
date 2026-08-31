# Privacy Model

> This document describes what the system does with data. It is written to be checkable
> against the code, not to reassure. Where the architecture cannot guarantee something,
> it says so.

## 1. The commitment

Veilix does not profile its users. There are no accounts, no identity cookies, no
cross-request user identifier, no search history, no advertising, and no third-party
analytics. Those are not settings that ship disabled — they are capabilities the system
does not contain.

## 2. Data inventory

### 2.1 What is never stored

| Data | Status |
|---|---|
| Raw IP addresses | Never written to disk or to Valkey. See §3. |
| Search query text | Held in memory for the duration of the request. Never persisted with any identifier. |
| Search history, per user or global | Not collected. There is no table, key, or file for it. |
| User accounts, emails, passwords | The system has no user accounts. |
| Tracking cookies or identity cookies | None set. |
| Browser fingerprints | Not computed. |
| Referrer data about where users came from | Not recorded. |
| Advertising or conversion identifiers | None. The system serves no ads. |

### 2.2 What is stored, and for how long

| Data | Where | Retention | Contains identity? |
|---|---|---|---|
| Rate-limit counter, keyed by `HMAC-SHA256(ip, daily_salt)` | Valkey | TTL equal to the limit window (minutes) | No — the salt is unrecoverable after rotation |
| Cached search results, keyed by hash of normalised query parameters | Valkey | Short TTL (minutes), configurable, can be disabled | No — the key has no identity component |
| Aggregate metrics: request counts, latency histograms, error counts, cache hit ratio | Prometheus | Prometheus retention window | No — counters and histograms only, never per-user series |
| Operational logs: timestamp, method, route template, status, duration, request ID | stdout, JSON | Whatever the host log driver keeps | No — see §4 for what is deliberately excluded |
| API key hashes and admin credential hash | Environment configuration | Life of the deployment | Operator identity, not user identity |

That is the complete list. If something is not in this table, the system does not keep it.

## 3. Rate limiting without storing IP addresses

Abuse prevention requires telling clients apart, and on the open internet clients are
told apart by IP address. This is a genuine tension with a no-tracking product, and the
resolution is worth understanding precisely.

The raw IP is used **only** as input to a keyed hash, in memory, and is never written
anywhere:

```
key = "rl:" + HMAC-SHA256(client_ip, salt_for_today)[:16]
```

- The salt is generated at random, held in memory, and **rotated daily**.
- The salt is never persisted. Once it rotates, the previous day's keys cannot be
  linked back to any IP address by anyone — including the operator, including someone
  who seizes the Valkey data.
- Each key carries a TTL equal to the rate-limit window, so entries expire in minutes
  regardless.

The effect: the system can count how many requests an anonymous client made in the last
minute, and cannot determine who that client was, or connect their activity today to
their activity yesterday. It can count you; it cannot remember you.

## 4. Logging discipline

Logs are structured JSON, and the schema is narrow. Every log line may
contain: timestamp, level, logger name, request ID, HTTP method, **route template**,
status code, and duration.

Explicitly excluded from logs:

- **Query text.** Never logged, at any level, including errors. An exception raised while
  handling a search must not put the query in a stack trace message.
- **Full request URLs.** The route *template* (`/api/v1/search`) is logged; the URL with
  its query string is not.
- **Client IP addresses.**
- **Request and response bodies.**
- **`User-Agent`, `Referer`, and `Cookie` headers.**

The request ID exists to correlate log lines *within a single request*, and is generated
per request. It is not a session identifier and cannot join two requests together.

## 5. What upstream search engines receive

This is where an honest privacy document has to be careful, because the guarantees are
weaker here and are not ours to make.

When you search, SearXNG queries upstream engines on your behalf. Those engines receive:

- **The query text.** They have to; it is the search.
- **Our server's IP address — not yours.** This is the core privacy benefit of
  meta-search: upstreams see the instance, not the user.
- A generic user agent, and no cookies from your browser.

They do **not** receive your IP, your cookies, your browser fingerprint, or a referrer
identifying you.

The caveat worth stating plainly: on a small self-hosted instance, the anonymity set is
small. If you are the only person using an instance, "the instance searched for X" and
"you searched for X" are the same statement to anyone who can observe the upstream side.
Meta-search hides you *in a crowd*, and a private instance has no crowd. This is a real
limitation of self-hosting, not a defect in the implementation.

## 6. Result rendering and third-party connections

Search results link to third-party sites, and image results reference third-party image
hosts. Left alone, a browser rendering those images would connect directly to
`gstatic.com`, `cdn.jsdelivr.net`, and dozens of others — handing each of them the user's
IP address and a referrer, for content the user never chose to load from them.

Two mitigations are enabled:

- **Image proxying**: thumbnails are fetched by our server and relayed, so third-party
  image hosts see the server, not the user. The cost is our bandwidth and CPU, which is
  accepted.
- **`Referrer-Policy: no-referrer`**: clicking through to a result does not tell the
  destination site that Veilix sent you.

**How image proxying actually works here, because the obvious setting is not enough.**
Setting `image_proxy: true` in SearXNG rewrites image URLs only when SearXNG renders its
own HTML templates. Veilix consumes the **JSON API**, and that output was measured to
contain raw third-party URLs — 0 of 264 image results came back proxied. Relying on the
setting alone would have left every image request going straight from the user's browser
to `pinimg.com`, `gstatic.com` and the rest, while the configuration file claimed
otherwise.

The Veilix API therefore performs the rewrite itself. SearXNG signs proxy URLs as
`HMAC-SHA256(secret_key, url)`, and the API holds the same secret, so it re-signs each
`img_src` into a proxy URL before the result ever reaches the browser. Verified working:
a signed request returns the image bytes with `HTTP 200`, and a tampered signature is
rejected with `HTTP 400`.

A residual risk this introduces, stated plainly. The image proxy fetches whatever
signed URL it is given. A hostile page that manages to rank in results could carry an
`img_src` pointing at an internal address, and our API would sign it. Upstream limits the
damage — responses must have an `image/` content type and are capped at 5 MB, so
exfiltration is constrained — but blind server-side request forgery remains possible.
Mitigation is scheduled for Phase 5 and tracked in
[`docs/security-findings.md`](./security-findings.md); it is recorded here rather than
left to be discovered later.

Once a user clicks a result, they are on someone else's site under someone else's privacy
policy. Nothing in this system extends past that boundary, and it does not pretend to.

## 7. Caching, and the side channel it creates

Cached results are keyed by a hash of the normalised query parameters alone — query,
category, language, time range, safe-search level, page. There is no identity component,
which is exactly what makes the cache shareable and therefore privacy-compatible: the
cache cannot distinguish who stored an entry.

The honest cost, stated, not buried: **a shared cache is a timing side channel.**
An attacker who can measure response latency can distinguish a cache hit from a miss, and
thereby learn that *somebody* searched a given term within the TTL window. They learn
nothing about who, when precisely, or how many people.

Mitigations and their limits:

- A short TTL bounds the observation window. It does not eliminate the channel.
- The cache can be disabled entirely by configuration, trading latency for the removal of
  this channel.

Eliminating the channel while keeping a shared cache is not possible in general. The
trade-off is documented so an operator can make the choice knowingly, instead of
discovering the property later.

## 8. Operational telemetry versus user surveillance

The difference is not a matter of intent, it is a matter of **cardinality and joinability**.

| Operational telemetry (what we do) | User surveillance (what we do not) |
|---|---|
| "Search latency p95 was 840 ms" | "This user's searches averaged 840 ms" |
| "There were 12,400 requests this hour" | "This visitor made 37 requests this hour" |
| "The cache hit ratio is 0.42" | "This user's queries hit cache 42% of the time" |
| "`brave` returned CAPTCHA on 8% of queries" | "This user's query to `brave` failed" |
| Counters and histograms with bounded label sets | Per-user time series, session IDs, user IDs |

The operational rule that keeps this honest: **no Prometheus label may carry a
user-derived value.** No IP, no hashed IP, no query text, no session identifier. Labels
are limited to bounded, operator-meaningful dimensions — route, method, status class,
engine name, cache outcome. A metric that cannot be sliced by user cannot be turned into
surveillance later, and label cardinality is the enforcement point.

## 9. What the operator can still technically observe

A privacy document that only lists strengths is marketing. The limits:

- **Caddy terminates TLS**, so plaintext queries exist in its memory during a request.
- **The API process handles every query** in plaintext to do its job.
- **An operator with root on the host** could attach a debugger, capture memory, enable
  packet capture on the internal network, or modify the code to log queries. No
  application-level design prevents this.
- The Valkey cache holds query hashes, and for a suspected query, an attacker with
  cache access could confirm presence by computing the hash. It is not reversible, but it
  is checkable against a guess.
- Upstream engines and the network path see what §5 describes.

What this system provides is a design that does not *retain* identifying data and does not
*want* it. What it cannot provide is protection against the operator of the machine it
runs on. If you do not trust the operator, self-host it yourself — which is precisely why
the project is built to be self-hostable.

**This project does not claim to be "100% anonymous", and any statement that it is would
be false.**

## 10. Compliance posture

Not legal advice, but a statement of the technical position:

- **Data minimisation** is achieved structurally, not by policy — the system has no
  place to put personal data.
- **Right to erasure** is trivially satisfied because there is nothing stored to erase.
  There is no user record to delete.
- **No third-party processors** receive user data. Upstream engines receive queries
  attributed to the server, as described in §5.
- **No cross-border transfer of personal data** occurs, because no personal data is
  retained to transfer.

An operator deploying this publicly still has their own obligations regarding, for
example, host-level access logs from their infrastructure provider — which sit outside
this application's control.
