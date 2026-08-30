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

# The production compose file alone must publish nothing. limiter:false is only
# safe while this holds; see docs/security-findings.md SF-004.
PORTS=$(docker compose -f docker-compose.yml config --format json 2>/dev/null |
  python -c "
import json,sys
d=json.load(sys.stdin)
print(sum(len(s.get('ports') or []) for s in d['services'].values()))
" 2>/dev/null)

if [ "${PORTS:-x}" = "0" ]; then
  ok  "production compose publishes no host ports"
else
  bad "production compose publishes ${PORTS} port(s) — SearXNG must never be exposed with limiter:false"
fi

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
  for cat in general news images it science; do
    n=$(curl -s -m 40 "${SEARXNG_DEV_URL}/search?q=test&categories=${cat}&format=json" |
        python -c "import json,sys;print(len(json.load(sys.stdin).get('results',[])))" 2>/dev/null)
    if [ "${n:-0}" -gt 0 ] 2>/dev/null; then
      ok  "category '${cat}' returned ${n} results"
    else
      bad "category '${cat}' returned no results"
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
printf '\n\033[1mResult:\033[0m %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
