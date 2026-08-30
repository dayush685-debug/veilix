# Veilix edge image: the built SPA baked into Caddy (ADR-0005).
#
# One container instead of a proxy plus a separate static server. The SPA and
# the routing that serves it ship as one versioned artefact, so they cannot
# drift apart between environments.

# ---------------------------------------------------------------------------
# Stage 1 — build the SPA
# ---------------------------------------------------------------------------
FROM node:24-alpine AS builder

WORKDIR /build

# Lockfile first: this layer is cached and only rebuilds when dependencies
# change, so an ordinary source edit skips the install entirely.
COPY apps/web/package.json apps/web/package-lock.json ./
# `npm ci` installs exactly what the lockfile pins, and fails rather than
# silently resolving a different tree the way `npm install` would.
RUN npm ci --no-audit --no-fund

COPY apps/web/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2 — Caddy with the built assets
# ---------------------------------------------------------------------------
FROM caddy:2-alpine AS runtime

# Apply the base image's outstanding package updates.
#
# Measured, not assumed: a Trivy scan of this image reported 21 HIGH/CRITICAL
# findings, all with fixes already published upstream - openssl, curl, c-ares.
# A pinned tag is a point-in-time snapshot, so building on it without an
# upgrade ships whatever was current when that tag was cut.
RUN apk --no-cache upgrade

# Strip the file capabilities from the Caddy binary.
#
# The image ships /usr/bin/caddy with cap_net_bind_service so it can bind port
# 80 as a non-root user. Under `cap_drop: ALL` the kernel refuses to exec a
# binary whose permitted capability set is not in the bounding set, and the
# container crash-loops with "exec /usr/bin/caddy: operation not permitted".
#
# Handing the capability back would work. Removing it is better: Caddy listens
# on 8080 here and Docker publishes 80/443 to it, so the capability was never
# needed — and stripping it means this binary cannot bind a privileged port at
# all, even if something later tries.
RUN apk add --no-cache libcap     && setcap -r /usr/bin/caddy     && apk del libcap

COPY infra/caddy/Caddyfile /etc/caddy/Caddyfile
COPY --from=builder /build/dist /srv

# Run as an unprivileged account.
#
# The official image runs Caddy as root. Nothing here needs it: the binary's
# port-binding capability was stripped above, it listens on 8080, and Docker
# publishes the privileged port to it.
#
# /data holds TLS private keys and the ACME account key, so it must be
# writable by this user and readable by nobody else. Creating the directories
# here with the right ownership matters because Docker initialises a fresh
# named volume from the image content it shadows - do it after this point and
# the volume comes up owned by root and Caddy cannot write its certificates.
RUN addgroup -g 10002 -S caddy 2>/dev/null || true     && adduser -u 10002 -S -G caddy caddy 2>/dev/null || true     && mkdir -p /data/caddy /config/caddy     && chown -R 10002:10002 /data /config     && chmod 700 /data

USER 10002:10002

# Node, npm, and the entire source tree stay in the builder. The runtime image
# holds static files and one binary.

EXPOSE 8080
