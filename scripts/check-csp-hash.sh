#!/usr/bin/env bash
#
# Fails if the inline theme script in index.html no longer matches the hash
# allowed by the Content-Security-Policy in the Caddyfile.
#
# Without this check the failure mode is nasty and quiet: someone edits the
# theme snippet, the CSP hash no longer matches, the browser silently refuses
# to run it, and the only symptom is a white flash for dark-mode users that
# nobody can reproduce on their own machine.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HTML="${ROOT}/apps/web/index.html"
CADDYFILE="${ROOT}/infra/caddy/Caddyfile"

ACTUAL=$(python - "$HTML" <<'PY'
import base64, hashlib, io, re, sys
html = io.open(sys.argv[1], encoding='utf-8').read()
match = re.search(r'<script>(.*?)</script>', html, re.S)
if not match:
    print('NO_INLINE_SCRIPT')
    raise SystemExit(0)
digest = hashlib.sha256(match.group(1).encode('utf-8')).digest()
print('sha256-' + base64.b64encode(digest).decode())
PY
)

if [ "$ACTUAL" = "NO_INLINE_SCRIPT" ]; then
  echo "OK: index.html has no inline script; the CSP hash is unused."
  exit 0
fi

if grep -qF "$ACTUAL" "$CADDYFILE"; then
  echo "OK: CSP hash matches the inline theme script."
  echo "    $ACTUAL"
  exit 0
fi

cat <<MSG
FAIL: the inline script in apps/web/index.html does not match any hash in the
      Caddyfile's Content-Security-Policy.

      The browser will silently refuse to run it, and dark-mode users will see
      a white flash on load that reproduces nowhere but production.

      Expected hash: $ACTUAL

      Update the script-src directive in infra/caddy/Caddyfile.
MSG
exit 1
