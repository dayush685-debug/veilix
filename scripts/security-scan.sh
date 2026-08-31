#!/usr/bin/env bash
#
# Dependency and container vulnerability scan.
#
#   ./scripts/security-scan.sh          fail on any FIXABLE high/critical finding
#   ./scripts/security-scan.sh --report print everything, never fail
#
# The gate is deliberately **fixable** findings, not all findings.
#
# Failing on every CVE regardless of whether a patch exists produces a build
# that cannot be made green by any action the team can take, and a gate nobody
# can satisfy is a gate everybody learns to bypass. A finding with a published
# fix is a decision we are declining to make; one without is a risk to record
# and monitor. Only the first should block a merge.

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPORT_ONLY=0
[ "${1:-}" = "--report" ] && REPORT_ONLY=1

FAILURES=0

ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAILURES=$((FAILURES + 1)); }
note() { printf '  \033[33mNOTE\033[0m  %s\n' "$1"; }
head_() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# ---------------------------------------------------------------------------
head_ "Python dependencies (pip-audit)"
# ---------------------------------------------------------------------------

# Overridable, because the project interpreter is usually a virtualenv that is
# not first on PATH:
#   PYTHON=/path/to/venv/bin/python ./scripts/security-scan.sh
PYTHON="${PYTHON:-python}"

PIP_AUDIT_OUT=$("${PYTHON}" -m pip_audit --progress-spinner off 2>&1 || true)

if echo "${PIP_AUDIT_OUT}" | grep -q "No module named"; then
  note "pip-audit not installed (pip install pip-audit) - skipping"
elif echo "${PIP_AUDIT_OUT}" | grep -q "No known vulnerabilities found"; then
  ok "no known vulnerabilities in Python dependencies"
else
  echo "${PIP_AUDIT_OUT}" | tail -20
  bad "pip-audit reported vulnerabilities"
fi


# ---------------------------------------------------------------------------
head_ "Node dependencies (npm audit)"
# ---------------------------------------------------------------------------

if [ -d "${ROOT}/apps/web/node_modules" ]; then
  AUDIT=$(cd "${ROOT}/apps/web" && npm audit --audit-level=high --json 2>/dev/null)
  COUNT=$(echo "${AUDIT}" | python -c "
import json,sys
try:
    d=json.load(sys.stdin)
    m=d.get('metadata',{}).get('vulnerabilities',{})
    print(m.get('high',0)+m.get('critical',0))
except Exception:
    print(0)
" 2>/dev/null)
  if [ "${COUNT:-0}" = "0" ]; then
    ok "no high or critical npm advisories"
  else
    bad "${COUNT} high/critical npm advisories"
  fi
else
  note "apps/web/node_modules absent - run npm ci first"
fi

# ---------------------------------------------------------------------------
head_ "Container images (Trivy)"
# ---------------------------------------------------------------------------

if ! docker image inspect veilix-api:local >/dev/null 2>&1; then
  note "images not built - run docker compose build"
else
  for image in veilix-api:local veilix-web:local; do
    # --ignore-unfixed plus --exit-code is the gate, expressed in trivy's own
    # terms rather than by parsing its JSON. Fewer moving parts, and the tool
    # already knows exactly what "fixable" means.
    if MSYS_NO_PATHCONV=1 docker run --rm \
         -v /var/run/docker.sock:/var/run/docker.sock \
         -v "${ROOT}/.trivyignore:/.trivyignore:ro" \
         aquasec/trivy:latest image --scanners vuln \
         --severity HIGH,CRITICAL --ignore-unfixed \
         --exit-code 1 --quiet "${image}" >/dev/null 2>&1; then
      ok "${image}: no fixable high/critical vulnerabilities"
    else
      MSYS_NO_PATHCONV=1 docker run --rm \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -v "${ROOT}/.trivyignore:/.trivyignore:ro" \
        aquasec/trivy:latest image --scanners vuln \
        --severity HIGH,CRITICAL --ignore-unfixed --quiet "${image}" 2>/dev/null |
        grep -E "^\|" | head -12
      bad "${image}: has fixable high/critical vulnerabilities"
    fi
  done
fi

# ---------------------------------------------------------------------------
head_ "Secrets"
# ---------------------------------------------------------------------------

# Committing a secret is not undone by deleting it later: it stays in history
# and must be treated as disclosed.
LEAKED=0
for pattern in '\.env$' '\.env\.secrets$' '\.pem$' '\.key$'; do
  if git -C "${ROOT}" ls-files | grep -qE "${pattern}"; then
    bad "a file matching ${pattern} is tracked by git"
    LEAKED=1
  fi
done
[ "${LEAKED}" = "0" ] && ok "no secret files are tracked by git"

# Matches the placeholder being ASSIGNED as a value, not merely mentioned.
#
# A plain grep for the string is a false positive generator: the constant that
# *detects* this placeholder necessarily contains it, and so do the comments
# warning about it. A check that cries wolf on its own defences gets ignored,
# which is worse than not having it.
PLACEHOLDER_RE="(SEARXNG_SECRET=|secret_key:[[:space:]]*[\"'])ultrasecretkey"
if git -C "${ROOT}" grep -qnIE "${PLACEHOLDER_RE}" -- ':!docs' ':!*.md'; then
  bad "upstream's placeholder secret is assigned as a real value somewhere tracked"
  git -C "${ROOT}" grep -nIE "${PLACEHOLDER_RE}" -- ':!docs' ':!*.md'
else
  ok "placeholder secret is never assigned as a value"
fi


# ---------------------------------------------------------------------------
printf '\n\033[1mResult:\033[0m %d failing check(s)\n' "${FAILURES}"
[ "${REPORT_ONLY}" = "1" ] && exit 0
[ "${FAILURES}" -eq 0 ] || exit 1
exit 0
