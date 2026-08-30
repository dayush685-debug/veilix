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

# Node, npm, and the entire source tree stay in the builder. The runtime image
# holds static files and one binary.

EXPOSE 8080
