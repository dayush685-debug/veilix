# Veilix API image.
#
# Multi-stage so that compilers and build headers exist only in the builder and
# never ship. argon2-cffi and orjson may build native extensions; those tools
# are a meaningful attack surface in a runtime image and are worth ~150 MB and
# a stage boundary to leave behind.

# ---------------------------------------------------------------------------
# Stage 1 — build dependencies into a self-contained virtualenv
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# build-essential is needed only if a wheel is unavailable for this platform.
# Kept in the builder stage exactly so it cannot reach the runtime image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Dependency metadata first: this layer is cached and rebuilds only when
# pyproject.toml changes, so ordinary source edits skip dependency resolution.
COPY pyproject.toml README.md* ./
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install dependencies against a stub package so the layer does not depend on
# application source. `pip install .` here would invalidate this cache on every
# source change, which is the mistake that makes Python image builds slow.
#
# The [otel] extra is included deliberately. Without it, setting
# VEILIX_OTLP_ENDPOINT does nothing at all - the SDK import fails and tracing
# stays off - which is a documented configuration knob that silently lies.
# Tracing still only EXPORTS when an endpoint is configured; this just means
# the capability exists when someone asks for it.
RUN mkdir -p src/veilix \
    && printf '__version__ = "0.0.0"\n' > src/veilix/__init__.py \
    && pip install --no-cache-dir ".[otel]"

COPY src/ ./src/

# --force-reinstall is load-bearing. Without it pip sees the stub already
# installed at the same version and skips this install entirely, shipping an
# image whose package is the empty placeholder from the layer above. It
# starts, serves, and reports version 0.0.0 — a failure that looks like
# success, which is the worst kind. Caught by the startup log line.
#
# The `rm -rf` is equally load-bearing: the stub install above leaves a
# populated build/ and *.egg-info behind, and setuptools reuses them rather
# than re-copying from src/. Without the clean, the real source is compiled
# against stale staged files and the placeholder __init__.py ships anyway.
# Verified the hard way — the image started, served traffic, and reported
# version 0.0.0.
RUN rm -rf build/ src/*.egg-info *.egg-info \
    && pip install --no-cache-dir --no-deps --force-reinstall ".[otel]"

# ---------------------------------------------------------------------------
# Stage 2 — runtime
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Apply the base image's outstanding security updates.
#
# Not cosmetic: a Trivy scan of this image reported 55 HIGH/CRITICAL OS
# findings, most of them util-linux issues with fixes already published. A
# pinned base tag is a point-in-time snapshot, so pinning it and never
# upgrading means shipping whatever was current when that tag was cut.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Fixed high uid/gid rather than a name. Numeric ids are what the kernel
# enforces, they survive a base-image change that renumbers accounts, and they
# let compose assert `user:` without depending on the image's /etc/passwd.
RUN groupadd --gid 10001 veilix \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin veilix

COPY --from=builder --chown=root:root /opt/venv /opt/venv

# Remove pip and setuptools from the runtime image.
#
# Both Python vulnerabilities Trivy reported (msgpack via pip's vendored
# cachecontrol, and setuptools itself) came from the packaging tooling rather
# than from anything this application imports. Nothing installs packages at
# runtime, so the tooling is pure attack surface: without it, an attacker with
# code execution cannot `pip install` a payload, and the image stops inheriting
# CVEs from a dependency tree it never uses.
RUN find /opt/venv /usr/local/lib/python3.13 -maxdepth 4 -type d \
        \( -name pip -o -name "pip-*" \
           -o -name setuptools -o -name "setuptools-*" \
           -o -name pkg_resources -o -name _distutils_hack \) \
        -exec rm -rf {} + 2>/dev/null || true \
    && rm -f /opt/venv/bin/pip* /usr/local/bin/pip* \
    && python -c "import veilix; print('runtime import OK:', veilix.__version__)"

WORKDIR /app
USER 10001:10001

EXPOSE 8000

# No HEALTHCHECK here on purpose. Compose defines it alongside the dependency
# graph and restart policy, so health lives in one place rather than being
# split between an image and an orchestrator that can disagree.

# Single worker: this workload is I/O-bound fan-out, so concurrency comes from
# the event loop rather than from processes. Multiple workers would multiply
# memory and give each its own circuit-breaker state, which is the scaling
# caveat recorded in infrastructure/circuit_breaker.py.
# --proxy-headers is deliberately NOT enabled.
#
# It would have uvicorn rewrite `request.client.host` from X-Forwarded-For, and
# with --forwarded-allow-ips "*" it would do so for any sender. The rate
# limiter treats client.host as the *untrusted* fallback and applies its own
# trust rule (api/middleware.py). Letting uvicorn rewrite that value first
# would quietly turn the untrusted fallback into attacker-controlled input,
# and the limiter would keep reporting numbers while limiting nothing.
#
# One layer owns the trust decision, and this is not that layer.
CMD ["uvicorn", "veilix.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--no-server-header", \
     "--no-access-log"]
