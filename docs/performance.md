# Performance and Reliability

> **Every number here was measured on the hardware described in §1.** Nothing is
> extrapolated, and nothing is a target dressed up as a result. Where a figure
> reflects the test environment rather than the system, it says so.

## 1. Test environment — read this before the numbers

| | |
|---|---|
| Host | Windows 11, 12 logical CPUs, **5.9 GB total RAM** |
| Docker VM | **2.77 GiB**, shared with unrelated containers from other projects |
| Load generator | k6, running in a container **on the same host** |
| Upstream | The real public internet, with real CAPTCHA blocks |

Three caveats that matter more than any figure below:

1. **The load generator competes with the system under test** for the same 12
   CPUs and the same 2.77 GiB. Throughput numbers are a floor, not a ceiling.
2. **Upstream latency is not ours.** Cold-search timings are dominated by how
   fast third-party search engines answer, which varies hour to hour and is
   outside our control.
3. **These are not production capacity numbers**, and this document will not
   pretend otherwise. They characterise behaviour and prove the mechanisms
   work; sizing a real deployment needs a test on real deployment hardware.

## 2. Latency

From the k6 capacity run (`tests/load/search.js`), 15 virtual users over 80
seconds, 1,218 requests, **0 failures, 0 rate-limited**:

| Path | median | p90 | p95 | max |
|---|---|---|---|---|
| **Warm** (cache hit) | **4.96 ms** | 8.09 ms | 10.37 ms | 1.30 s |
| **Cold** (upstream fan-out) | 795 ms | 5.23 s | 5.94 s | 6.65 s |

Single-request measurements through the edge, for comparison:

```
cold   1.02 – 6.35 s     (varies with which engines answer)
warm   9.6 – 19.7 ms
index.html   36 ms, 896 B over the wire (1,502 B raw, zstd)
```

**The cache is the difference between two different products.** Roughly 100×
on the median. It is also why the cache-versus-privacy trade-off in
[`privacy.md` §7](privacy.md) was worth thinking about carefully instead of
switching on by reflex.

### What actually dominates cold latency

Not our code. A cold search waits for upstream engines, and SearXNG waits up to
its per-engine timeout (5 s) for stragglers. So **p95 cold latency ≈ the engine
timeout**, and the tuning knob is a straight trade:

- Lower `outgoing.request_timeout` → faster p95, fewer engines represented.
- Higher → slower p95, more complete results.

It is set to 5 s, above upstream's 3 s default, because slow-but-useful engines
(arxiv, semantic scholar) were being cut off. That is a deliberate choice to
prefer completeness over tail latency, and it is reversible in one line.

The API's own overhead is visible in the warm path: **~5 ms median** covers
Caddy, the rate limiter, a Valkey round trip, and JSON serialisation.

## 3. Throughput, and the honest reading of it

Two runs, because the first measured the wrong thing:

| Run | Config | Throughput | Rate-limited | Errors |
|---|---|---|---|---|
| Default limits | 600 req/min per key | 149 req/s attempted | **92.6%** | — |
| Limiter raised | effectively unlimited | **15.2 req/s sustained** | 0% | **0%** |

**The first run is the more interesting result.** k6 pushed 149 req/s against a
10 req/s budget and the limiter absorbed all of it, correctly, while liveness
kept answering 200 throughout. The system's ceiling under default configuration
is the rate limiter — by design.

The second run measures the search path with that ceiling removed: **15.2 req/s
with zero errors**, bounded by upstream fan-out rather than by anything in this
codebase. On a host that is also running the load generator and three unrelated
projects.

**What this does not tell you**: what a real VPS would sustain. Do not quote
15 req/s as a capacity figure — it is a measurement of this laptop.

## 4. Degradation is the normal case

`veilix_degraded_responses: 100%` across both runs. Every single search returned
results while at least one upstream engine was unavailable.

That is not a fault, it is the steady state for a self-hosted instance — large
engines CAPTCHA and rate-limit them. It is why the API reports `degraded` and
names the failing engines instead of quietly returning less, and why
[ADR-0006](adr/0006-one-circuit-breaker-not-per-engine.md) leaves per-engine
back-off to SearXNG, which already classifies failures by type.

## 5. Failure behaviour, measured

SearXNG was stopped outright and the system observed.

**A cached query kept being served, in 86 ms, with the search backend
completely down.**

That validates a specific ordering decision: the cache is consulted *before* the
circuit breaker. Reversing the two would have turned a recoverable upstream
outage into a total one for queries already answerable.

Uncached queries, with the breaker starting closed:

```
req 1   503  7.70 s   upstream-unavailable
req 2   503  7.70 s   upstream-unavailable
req 3   503  7.71 s   upstream-unavailable
req 4   503  7.72 s   upstream-unavailable
req 5   503  7.70 s   upstream-unavailable
req 6   503  0.012 s  circuit-open   retry_after=29
req 7   503  0.007 s  circuit-open   retry_after=29
```

**Requests 1–5 take 7.7 s each; request 6 takes 12 ms.** Once the breaker opens,
a shed request is ~640× cheaper and never touches the dead dependency. That is
the entire point of load shedding, and here it is with numbers.

The two problem types are deliberately distinct: `upstream-unavailable` is a
failure the backend reported; `circuit-open` is Veilix choosing to shed. A
dashboard that conflated them could not tell an outage from a system correctly
protecting itself.

**Recovery is automatic.** SearXNG was restarted, the 30 s window elapsed, one
probe was admitted, it succeeded, and the breaker closed. Metrics recorded the
whole lifecycle:

```
veilix_breaker_transitions_total{to_state="open"}       1.0
veilix_breaker_transitions_total{to_state="half_open"}  1.0
veilix_breaker_transitions_total{to_state="closed"}     1.0
veilix_breaker_state{dependency="searxng"}              0.0   (closed)
```

Probe semantics held throughout: `/ready` returned 503 while the backend was
down, `/live` stayed 200. A dead dependency is not a dead process, and
conflating them turns a Valkey hiccup into a restart storm.

## 6. Optimisations that are in place

- **Connection pooling** — one `httpx.AsyncClient` for the process (100
  connections, 20 keep-alive). A fresh TCP and TLS handshake per search would
  add latency to every request.
- **Async throughout.** The workload is I/O-bound fan-out, so concurrency comes
  from the event loop. One worker per container: more would multiply memory on a
  small host and give each its own breaker state.
- **Response compression** — zstd and gzip at the edge. `index.html` goes out at
  896 B versus 1,502 B raw.
- **Cache-Control split** — hashed assets `immutable, max-age=31536000`;
  `index.html` `no-cache`, so a deploy cannot pin clients to a stale bundle that
  references assets which no longer exist.
- **Bundle** 88 kB gzipped, with the vendor chunk split so React and the router
  stay cached across deploys.
- **`loading` is derived**, not stored (`useSearch`), which makes the
  stuck-spinner state unrepresentable rather than merely unlikely.

## 7. Known bottlenecks, in order

1. **Upstream engine latency.** Dominates cold search, is not ours, and is
   bounded only by the timeout. The only real lever is the cache.
2. **Rate limiting.** The intentional ceiling. Raising it raises abuse exposure
   and the risk of upstream bans.
3. **Single API worker.** Fine for an I/O-bound workload at this scale;
   horizontal scaling would need the breaker and rate-limit salt shared, which
   is designed for but not built.
4. **Failed-request latency (7.7 s).** A connection failure costs a connect
   timeout plus one retry. The breaker makes this irrelevant under sustained
   failure, but the first few requests of an outage are slow. Lowering the
   connect timeout would help and has not been done, because the sustained case
   is what matters and the breaker already covers it.

## 8. Reproducing this

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

docker run --rm -i --network veilix_edge \
  -e BASE_URL=http://caddy:8080 \
  -e API_KEY="$YOUR_KEY" \
  grafana/k6 run - < tests/load/search.js
```

Without an API key the run measures the rate limiter instead of the search
path — which is a legitimate thing to measure, but a different one.
