# Edge image: the built SPA baked into Caddy, so the assets and the routing
# that serves them ship as one artefact (ADR-0005).

FROM node:24-alpine AS builder

WORKDIR /build

# Lockfile first so this layer survives ordinary source edits.
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY apps/web/ ./
RUN npm run build


FROM caddy:2-alpine AS runtime

# A pinned tag is a point-in-time snapshot. Trivy found 21 HIGH/CRITICAL in
# this base, all with published fixes.
RUN apk --no-cache upgrade

# The binary ships with cap_net_bind_service so it can bind :80 unprivileged.
# Under `cap_drop: ALL` the kernel refuses to exec it at all, and the container
# crash-loops on "operation not permitted". We listen on 8080 and let Docker
# publish 80/443, so the capability is not needed; removing it beats granting
# it back.
RUN apk add --no-cache libcap \
    && setcap -r /usr/bin/caddy \
    && apk del libcap

COPY infra/caddy/Caddyfile /etc/caddy/Caddyfile
COPY --from=builder /build/dist /srv

# /data holds TLS private keys, so it must be writable by this user and nobody
# else. Ownership has to be set before the volume is declared: Docker seeds a
# new named volume from the image content it shadows, and doing this later
# leaves the volume root-owned with Caddy unable to write certificates.
RUN addgroup -g 10002 -S caddy 2>/dev/null || true \
    && adduser -u 10002 -S -G caddy caddy 2>/dev/null || true \
    && mkdir -p /data/caddy /config/caddy \
    && chown -R 10002:10002 /data /config \
    && chmod 700 /data

USER 10002:10002

EXPOSE 8080
