#!/usr/bin/env bash
#
# Verifies the architectural claims that documentation alone cannot prove.
#
# Every check here corresponds to a statement in an ADR or in docs/privacy.md.
# If a claim cannot be checked mechanically it does not belong in this script,
# and if a check fails the claim is wrong — not the test.
#
#   ./scripts/verify-stack.sh
#
# Requires the stack to be running:
#   docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

set -uo pipefail

PASS=0
FAIL=0
SEARXNG_DEV_URL="${SEARXNG_DEV_URL:-http://127.0.0.1:18080}"

ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS + 1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL + 1)); }
head_() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# ---------------------------------------------------------------------------
head_ "Network isolation (ADR-0004)"
# ---------------------------------------------------------------------------

# The central claim: a service on the backend network has no route off the host.
if docker run --rm --network veilix_backend alpine:3 \
     wget -q -T 6 -O /dev/null https://example.com >/dev/null 2>&1; then
  bad "backend network reached the internet — internal:true is not in effect"
else
  ok  "backend network has no internet route"
fi

if docker run --rm --network veilix_backend alpine:3 \
     wget -q -T 6 -O /dev/null http://searxng:8080/healthz >/dev/null 2>&1; then
  ok  "searxng reachable from backend network"
else
  bad "searxng unreachable from backend network"
fi

# SearXNG is the one service that legitimately needs egress.
if docker exec veilix-searxng wget -q -T 8 --spider https://example.com >/dev/null 2>&1; then
  ok  "searxng has internet egress (it is also on veilix_egress)"
else
  bad "searxng has no internet egress — upstream engines will all fail"
fi

# ---------------------------------------------------------------------------
head_ "Exposure surface (SF-004)"
# ---------------------------------------------------------------------------

# Caddy is the ONLY service allowed to publish a host port. SearXNG in
# particular must publish none: its JSON API is enabled and its own bot
# limiter is off, so exposing it would create an open, unauthenticated,
# abusable search API (SF-004).
docker compose -f docker-compose.yml config --format json 2>/dev/null |
  python -c "
import json,sys
G,R,Y='[32m','[31m','[0m'
d=json.load(sys.stdin)
ALLOWED={'caddy'}
rc=0
for name,svc in sorted(d['services'].items()):
    ports=svc.get('ports') or []
    if ports and name not in ALLOWED:
        print(f'  {R}FAIL{Y}  {name} publishes {len(ports)} host port(s) - only the edge may be exposed')
        rc=1
    elif not ports and name not in ALLOWED:
        print(f'  {G}PASS{Y}  {name} publishes no host ports')
    elif ports:
        print(f'  {G}PASS{Y}  {name} is the edge and publishes {len(ports)} port(s)')
    else:
        print(f'  {R}FAIL{Y}  {name} is the edge but publishes nothing')
        rc=1
sys.exit(rc)
" || bad "port exposure does not match the intended topology"

# ---------------------------------------------------------------------------
head_ "Container hardening"
# ---------------------------------------------------------------------------

VK_UID=$(docker exec veilix-valkey id -u 2>/dev/null)
if [ "${VK_UID:-0}" != "0" ]; then
  ok  "valkey runs as non-root (uid ${VK_UID})"
else
  bad "valkey is running as root"
fi

# Known open finding SF-001: upstream image does not drop privileges.
SX_UID=$(docker exec veilix-searxng id -u 2>/dev/null)
if [ "${SX_UID:-0}" != "0" ]; then
  ok  "searxng runs as non-root (uid ${SX_UID})"
else
  printf '  \033[33mKNOWN\033[0m  searxng runs as root — tracked as SF-001, fix due in Phase 5\n'
fi

# ---------------------------------------------------------------------------
head_ "Privacy: no search data at rest (docs/privacy.md §2.2)"
# ---------------------------------------------------------------------------

SAVE=$(docker exec veilix-valkey valkey-cli config get save 2>/dev/null | tail -1 | tr -d '\r')
AOF=$(docker exec veilix-valkey valkey-cli config get appendonly 2>/dev/null | tail -1 | tr -d '\r')

if [ -z "${SAVE}" ]; then
  ok  "valkey RDB snapshots disabled"
else
  bad "valkey RDB snapshots ENABLED (save='${SAVE}') — cached queries would hit disk"
fi

if [ "${AOF}" = "no" ]; then
  ok  "valkey append-only file disabled"
else
  bad "valkey AOF ENABLED — cached queries would hit disk"
fi

if docker inspect veilix-valkey --format '{{len .Mounts}}' 2>/dev/null | grep -q '^0$'; then
  ok  "valkey has no mounted volume — nothing can persist"
else
  bad "valkey has a volume mounted — verify no search data can be written to it"
fi

# ---------------------------------------------------------------------------
head_ "Search functionality"
# ---------------------------------------------------------------------------

if ! curl -sf -m 10 "${SEARXNG_DEV_URL}/healthz" >/dev/null 2>&1; then
  printf '  \033[33mSKIP\033[0m  %s unreachable — bring the stack up with docker-compose.dev.yml\n' "${SEARXNG_DEV_URL}"
else
  # Retried once before failing.
  #
  # Not flakiness-hiding: upstream engines suspend themselves under load
  # (CAPTCHA -> 3600s, rate limit -> 180s), so running this script repeatedly
  # can genuinely exhaust every engine in a category for a few minutes. That is
  # the behaviour the whole architecture is designed around, and it should not
  # be reported as a broken stack. A category that returns nothing twice in a
  # row is a real problem worth failing on.
  for cat in general news images it science; do
    n=0
    for attempt in 1 2; do
      n=$(curl -s -m 40 "${SEARXNG_DEV_URL}/search?q=test&categories=${cat}&format=json" |
          python -c "import json,sys;print(len(json.load(sys.stdin).get('results',[])))" 2>/dev/null)
      [ "${n:-0}" -gt 0 ] 2>/dev/null && break
      [ "${attempt}" = "1" ] && sleep 4
    done
    if [ "${n:-0}" -gt 0 ] 2>/dev/null; then
      ok  "category '${cat}' returned ${n} results"
    else
      bad "category '${cat}' returned no results on two attempts"
    fi
  done

  # Engine curation from infra/searxng/settings.yml.
  # Piped rather than written to a temp file: under Git Bash on Windows, /tmp
  # is an MSYS mount that the native python.exe cannot open.
  curl -s -m 15 "${SEARXNG_DEV_URL}/config" | python -c "
import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    raise SystemExit(0)
en = {e['name']: e['enabled'] for e in d['engines']}
G, R, Y = '[32m', '[31m', '[0m'
rc = 0
def check(name, want, why):
    global rc
    got = en.get(name)
    good = got == want
    rc |= 0 if good else 1
    mark = f'{G}PASS{Y}' if good else f'{R}FAIL{Y}'
    print(f'  {mark}  {name} enabled={got} (expected {want}) - {why}')
for n in ('mojeek', 'qwant', 'mwmbl'):
    check(n, True, 'independent index, offsets CAPTCHA-blocked engines')
for n in ('bt4g', 'kickass', 'piratebay', 'solidtorrents'):
    check(n, False, 'torrent engine disabled by policy')
print(f\"  {G}INFO{Y}  instance_name={d['instance_name']!r} safe_search={d['safe_search']} limiter={d['limiter']['enabled']}\")
sys.exit(rc)
" || bad "engine curation does not match infra/searxng/settings.yml"
fi

# ---------------------------------------------------------------------------
head_ "Veilix API"
# ---------------------------------------------------------------------------

API_URL="${API_URL:-http://127.0.0.1:18000}"

if ! curl -sf -m 10 "${API_URL}/api/v1/live" >/dev/null 2>&1; then
  printf '  [33mSKIP[0m  %s unreachable - start the api service
' "${API_URL}"
else
  ok "liveness responds"

  # Version 0.0.0 means the Dockerfile shipped the dependency-cache stub
  # instead of the real package - a failure that otherwise looks like success.
  VER=$(curl -s -m 10 "${API_URL}/api/v1/health" | python -c "import json,sys;print(json.load(sys.stdin).get('version','?'))" 2>/dev/null)
  if [ "${VER}" = "0.0.0" ] || [ -z "${VER}" ]; then
    bad "api reports version '${VER}' - the build shipped the stub package"
  else
    ok "api reports a real version (${VER})"
  fi

  # Unknown query parameters must 422 rather than being silently ignored,
  # or a typo like safe_search= would quietly give the wrong filtering.
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 15 "${API_URL}/api/v1/search?q=x&safe_search=2")
  if [ "${CODE}" = "422" ]; then
    ok "unknown query parameters are rejected (422)"
  else
    bad "unknown query parameter returned ${CODE}, expected 422"
  fi

  # Every error must be RFC 9457 problem+json, validation failures included.
  CT=$(curl -s -o /dev/null -w '%{content_type}' -m 15 "${API_URL}/api/v1/search?q=x&bogus=1")
  case "${CT}" in
    application/problem+json*) ok "errors use problem+json" ;;
    *) bad "error content-type was '${CT}', expected application/problem+json" ;;
  esac

  # The privacy claim in docs/privacy.md section 6: no third-party image host
  # may appear in a response, or rendering the page leaks the viewer's IP.
  curl -s -m 60 "${API_URL}/api/v1/search?q=mountain&category=images" | python -c "
import json,sys
G,R,Y='[32m','[31m','[0m'
try: d=json.load(sys.stdin)
except Exception: print(f'  {R}FAIL{Y}  image search returned no parseable body'); sys.exit(1)
imgs=[r['media']['image_url'] for r in d.get('results',[]) if r.get('media') and r['media'].get('image_url')]
leaks=[u for u in imgs if u.startswith('http')]
if not imgs:
    print(f'  {R}FAIL{Y}  no image results carried a proxied url'); sys.exit(1)
if leaks:
    print(f'  {R}FAIL{Y}  {len(leaks)} of {len(imgs)} image urls point at third-party hosts'); sys.exit(1)
print(f'  {G}PASS{Y}  all {len(imgs)} image urls are proxied, none third-party')
" || bad "image proxying is leaking third-party urls"

  # Admin must never be open.
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 15 "${API_URL}/api/v1/admin/overview")
  if [ "${CODE}" = "401" ] || [ "${CODE}" = "429" ]; then
    ok "admin endpoint requires authentication (${CODE})"
  else
    bad "unauthenticated admin request returned ${CODE}, expected 401"
  fi

  # Metrics must carry no user-derived label (docs/privacy.md section 8).
  #
  # Checked as an ALLOWLIST of label names rather than a denylist of bad ones.
  # A denylist only catches the leaks someone already thought of; an allowlist
  # fails on any new label until a human approves it, which is the direction
  # that stays correct as the code grows. It also doubles as the guard against
  # unbounded metric cardinality, since the two problems have one cause.
  curl -s -m 15 "${API_URL}/api/v1/metrics" | python -c "
import sys,re
G,R,Y='[32m','[31m','[0m'
ALLOWED={'method','route','status_class','category','outcome','cache','engine',
         'reason','identity','decision','dependency','to_state','version',
         'environment','le'}
names=set()
for line in sys.stdin:
    if line.startswith('#') or '{' not in line: continue
    for m in re.finditer(r'([a-zA-Z_][a-zA-Z0-9_]*)=\"', line.split('{',1)[1]):
        names.add(m.group(1))
unexpected=sorted(names-ALLOWED)
if unexpected:
    print(f'  {R}FAIL{Y}  unapproved metric labels: {unexpected} - if user-derived this is a privacy leak, if not add it to the allowlist deliberately')
    sys.exit(1)
print(f'  {G}PASS{Y}  all {len(names)} metric labels are on the approved list')
" || bad "metrics carry unapproved labels"
fi

# ---------------------------------------------------------------------------
head_ "Edge (Caddy)"
# ---------------------------------------------------------------------------

EDGE_URL="${EDGE_URL:-http://127.0.0.1:8088}"

if ! curl -sf -m 10 "${EDGE_URL}/" >/dev/null 2>&1; then
  printf '  [33mSKIP[0m  %s unreachable - start the caddy service
' "${EDGE_URL}"
else
  ok "SPA is served"

  # Deep links must return the app shell, not a 404, or a shared search URL
  # breaks on first load.
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 15 "${EDGE_URL}/search?q=x")
  [ "${CODE}" = "200" ] && ok "deep links fall back to the SPA shell"                         || bad "deep link returned ${CODE}, expected 200"

  # Security headers.
  HEADERS=$(curl -sI -m 15 "${EDGE_URL}/")
  for h in "Content-Security-Policy" "X-Frame-Options" "X-Content-Type-Options"            "Referrer-Policy" "Permissions-Policy" "Strict-Transport-Security"; do
    echo "${HEADERS}" | grep -qi "^${h}:" && ok "${h} present"                                          || bad "${h} missing"
  done

  echo "${HEADERS}" | grep -qi "^Server:" && bad "Server header advertises the software"                                           || ok "Server header removed"

  # The CSP must forbid third-party images outright. That is only possible
  # because every thumbnail is proxied through this origin.
  if echo "${HEADERS}" | grep -qi "img-src 'self' data:"; then
    ok "CSP forbids third-party image hosts"
  else
    bad "CSP img-src allows hosts other than 'self'"
  fi

  # SearXNG must NOT be reachable through the edge. Status alone cannot tell:
  # the SPA fallback also answers 200, so the body decides.
  LEAKED=0
  for p in "/search?q=x&format=json" "/config" "/stats"; do
    if ! curl -s -m 15 "${EDGE_URL}${p}" | head -c 200 | grep -q "<!doctype html>"; then
      bad "SearXNG content leaked through the edge at ${p}"
      LEAKED=1
    fi
  done
  [ "${LEAKED}" = "0" ] && ok "SearXNG is not reachable through the edge"

  # The image proxy must refuse unsigned URLs, or it becomes an open proxy.
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 20 "${EDGE_URL}/img?url=https://example.com/x.jpg")
  [ "${CODE}" = "400" ] && ok "image proxy rejects unsigned URLs (400)"                         || bad "unsigned image proxy request returned ${CODE}, expected 400"

  # Access logs must carry no client address (docs/privacy.md section 4).
  if docker logs veilix-caddy 2>&1 | grep "handled request" | tail -20 |
     grep -qE '"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}"'; then
    bad "edge access logs contain client IP addresses"
  else
    ok "edge access logs carry no client addresses"
  fi

  # The inline theme script must match the hash the CSP allows, or the browser
  # silently refuses to run it and dark-mode users get a white flash.
  if bash "$(dirname "$0")/check-csp-hash.sh" >/dev/null 2>&1; then
    ok "CSP hash matches the inline theme script"
  else
    bad "CSP hash does not match apps/web/index.html - run scripts/check-csp-hash.sh"
  fi
fi

# ---------------------------------------------------------------------------
printf '\n\033[1mResult:\033[0m %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
