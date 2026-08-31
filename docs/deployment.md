# Deployment

> Verified by actually running it. The production configuration was started
> locally with a real TLS certificate path, and the one bug that found is
> documented in §7 rather than left for whoever deploys first.

## 1. What you need

| Requirement | Why |
|---|---|
| A host with **2 GB RAM** | Measured: the core stack idles at 187 MiB and reached 414 MiB after sustained use. 1 GB will not survive SearXNG's engine initialisation. |
| **20 GB disk** | Images are ~950 MB; the rest is headroom for logs and Prometheus. |
| Docker Engine 24+ and Compose v2 | |
| A **public DNS A/AAAA record** pointing at the host | Automatic certificates need it. |
| **Ports 80 and 443 reachable** | The ACME HTTP-01 challenge is served on 80. |

Everything here runs on free and open-source software. **Nothing in this stack
requires a paid service** — no managed database, no hosted APM, no search API
keys. The only recurring cost is the host itself, and a €4/month VPS is
sufficient.

## 2. Deploy

```bash
git clone <your-fork> veilix && cd veilix
cp .env.example .env
```

Generate the secrets — do not invent them by hand:

```bash
python scripts/hash_secret.py --secret           # SEARXNG_SECRET / salt seed
python scripts/hash_secret.py --admin-password   # admin password hash
python scripts/hash_secret.py --api-key          # an API key and its digest
```

Set these in `.env`, or in an optional `.env.secrets` beside it. Compose reads
`.env.secrets` when it exists and ignores it when it does not, which lets
credential material be rotated separately from ordinary configuration. Both are
gitignored.

```dotenv
VEILIX_ENV=production
VEILIX_SITE_ADDRESS=search.example.com          # your hostname, NOT :8080
VEILIX_PUBLIC_URL=https://search.example.com
VEILIX_ACME_EMAIL=you@example.com               # optional, for expiry notices

SEARXNG_SECRET=<generated>
VEILIX_RATELIMIT_SALT_SEED=<generated>
VEILIX_ADMIN_PASSWORD_HASH=<generated>
VEILIX_API_KEY_HASHES=<digest>                  # optional
```

Then:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Certificates are issued automatically on first request. Nothing else to do —
no certbot, no cron job, no renewal hook.

### Verify

```bash
./scripts/verify-stack.sh          # 38 architectural and security assertions
curl -I https://search.example.com # expect 200 and the security headers
curl -I http://search.example.com  # expect 308 to https
```

## 3. Configuration that will stop you starting

Production validation is strict, and it fails **at startup, loudly,
listing every problem at once**, not one per restart:

- `SEARXNG_SECRET` shorter than 32 characters, or upstream's `ultrasecretkey`
- `VEILIX_RATELIMIT_SALT_SEED` shorter than 32 characters
- `VEILIX_ADMIN_PASSWORD_HASH` unset — admin access **fails closed**, never open
- `VEILIX_RATELIMIT_ENABLED=false`
- `VEILIX_CORS_ORIGINS=*`

Each of these produces an instance that starts, serves traffic, reports healthy,
and is insecure. That is the worst failure mode available, so the check refuses
the deploy instead.

**`VEILIX_SITE_ADDRESS` must be a hostname**, not `:8080`. Caddy requests a
certificate for whatever is there, so a bare port means plain HTTP with no TLS —
a silent and total failure.

## 4. What is exposed

Only Caddy publishes ports. The API, SearXNG, and Valkey are unreachable from
outside, and CI fails the build if that ever changes.

This is load-bearing, not tidiness: SearXNG runs with its JSON API enabled and
its own bot limiter **off**, which is correct only while it is unpublished. If
its port were exposed it would become an open, unauthenticated, abusable search
API within hours (SF-004).

## 5. Backup and recovery

**Almost nothing here needs backing up, and that is by design.**

| Data | Backup? | Why |
|---|---|---|
| Search history | — | Does not exist |
| User accounts | — | Does not exist |
| Valkey cache and rate-limit counters | **No** | Deliberately memory-only, no volume mounted. Losing it costs a cold cache for a few minutes. |
| SearXNG caches | **No** | tmpfs, rebuilt on start |
| `caddy_data` (TLS certs, ACME account key) | **Optional** | Certificates re-issue automatically. Back it up only to avoid re-issuance rate limits during frequent rebuilds. |
| `prometheus_data`, `grafana_data` | Optional | Operational history; losing it loses graphs, not service |
| **`.env`** | **YES — this is the one** | Contains every secret. Not in git, and not recoverable. |

So the entire backup story is:

```bash
# Store this somewhere safe and encrypted. It is the only irreplaceable file.
cp .env /secure/backup/veilix.env.$(date +%F)

# Optional: avoid ACME re-issuance on frequent rebuilds
docker run --rm -v veilix_caddy_data:/data -v "$PWD":/backup alpine \
  tar czf /backup/caddy-data.tgz -C /data .
```

**Recovery** is a clone, the `.env`, and `up -d`. There is no database to
restore, no migration to replay, and no state to reconcile — which is a direct
consequence of [ADR-0002](adr/0002-no-relational-database.md). A system that
stores nothing is a system that cannot lose anything.

Losing `.env` means: rotating `SEARXNG_SECRET` invalidates in-flight image-proxy
URLs (harmless, they regenerate), rotating the salt seed resets rate-limit
buckets (harmless), and the admin hash must be regenerated. Nothing user-facing
is lost.

## 6. Updating

```bash
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Compose replaces containers one at a time; the edge stays up while the API
restarts. There is **no zero-downtime story** on a single host, and pretending
otherwise would be dishonest — expect a few seconds of 502 during an API
replacement. Genuine zero downtime needs two API replicas behind the proxy,
which also requires the shared rate-limit salt discussed in ADR-0003.

Update base images regularly. The Dockerfiles apply OS security updates at build
time, so a rebuild is what picks them up:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml build --pull
./scripts/security-scan.sh
```

## 7. A bug this document exists because of

The production configuration was tested by running it, not by reading it. That
immediately surfaced one:

**Caddy reported `unhealthy` forever in production.** The base healthcheck probes
`127.0.0.1:8080`, which is where Caddy listens when `VEILIX_SITE_ADDRESS` is a
bare port. With a real hostname it binds 80 and 443 instead, so the check could
never succeed — `depends_on: service_healthy` would block dependent services and
an orchestrator would restart an edge that was serving traffic correctly.

The fix is a healthcheck override in `docker-compose.prod.yml`. It is worth
reading, because the obvious version is also wrong: a plain `wget --spider` on
port 80 **follows** the 308 into HTTPS and fails the handshake, since the
container does not trust Caddy's own CA. busybox `wget` has neither
`--max-redirect` nor `--no-check-certificate`, so the check asserts the redirect
itself — which is precisely what a healthy auto-HTTPS Caddy produces.

## 8. Observability in production

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
               --profile observability up -d
```

Costs roughly as much memory as the entire core stack, which is why it is
opt-in. Neither Prometheus nor Grafana publishes a port in production. Reach
Grafana over an SSH tunnel:

```bash
ssh -L 3000:localhost:3000 you@host   # then open http://localhost:3000
```

Prometheus is unreachable even from the host by design — it has no
authentication at all. Query it through Grafana, or:

```bash
docker exec veilix-prometheus wget -qO- http://127.0.0.1:9090/api/v1/targets
```

## 9. Operational reality

**Expect degraded results, permanently.** Upstream engines rate-limit and
CAPTCHA self-hosted instances. On a datacentre IP this is worse than on a
residential one. The interface reports which engines failed instead of hiding
it, and the `SearchBackendCircuitOpen` alert fires only when SearXNG itself is
unwell — not when individual engines are blocked, which is the normal state.

Rate limits default to 60/minute anonymous. Raise
`VEILIX_RATELIMIT_REQUESTS` only if you understand that a scraped instance gets
banned upstream, and the instance dies without anything being breached.

Logs contain no query text and no IP addresses, so ordinary debugging is
harder by design. The request ID in an error response is the bridge to the
server-side log line.

## 10. Scaling, honestly

Not implemented, and not claimed. The design is scale-*ready* — the API is
stateless and Valkey is shared — but two things must change before a second
replica works correctly, both already documented:

- The **circuit breaker is per process** (`infrastructure/circuit_breaker.py`),
  so the effective failure threshold multiplies by the replica count.
- The **rate-limit salt must be shared**, or each replica derives a different one
  and enforces its own separate limit (ADR-0003).

Neither is hard; neither has been built or measured, so neither is claimed.
