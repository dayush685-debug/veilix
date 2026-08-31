# Multi-stage so compilers and headers stay in the builder. argon2-cffi and
# orjson may build native extensions, and that toolchain is not worth shipping.

FROM python:3.13-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY pyproject.toml README.md* ./
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Resolve dependencies against a stub so this layer survives source edits.
#
# The [otel] extra is not optional here: without it the SDK import fails,
# tracing stays off, and VEILIX_OTLP_ENDPOINT becomes a setting that does
# nothing and says nothing. Export still only happens when it is configured.
RUN mkdir -p src/veilix \
    && printf '__version__ = "0.0.0"\n' > src/veilix/__init__.py \
    && pip install --no-cache-dir ".[otel]"

COPY src/ ./src/

# Both halves of this are load-bearing, and the failure mode is nasty: the
# image starts, serves traffic, and reports version 0.0.0.
#
#   --force-reinstall  pip otherwise sees the stub at the same version and
#                      skips the install entirely.
#   rm -rf build/      setuptools reuses the stub's staged tree instead of
#                      re-copying src/, so the placeholder ships anyway.
RUN rm -rf build/ src/*.egg-info *.egg-info \
    && pip install --no-cache-dir --no-deps --force-reinstall ".[otel]"


FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# A pinned tag is a point-in-time snapshot. Trivy found 55 HIGH/CRITICAL in
# this base, mostly util-linux, nearly all with published fixes.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Numeric uid/gid: it is what the kernel enforces, it survives a base image
# renumbering its accounts, and compose can assert `user:` against it.
RUN groupadd --gid 10001 veilix \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin veilix

COPY --from=builder --chown=root:root /opt/venv /opt/venv

# Nothing installs packages at runtime, so pip is pure attack surface, and both
# Python CVEs Trivy reported were in it (msgpack via vendored cachecontrol, and
# setuptools). Removing it also means RCE cannot `pip install` a payload.
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

# Health lives in compose, next to the dependency graph and restart policy.

# One worker: the workload is I/O-bound fan-out, so concurrency comes from the
# event loop. More workers would multiply memory and give each its own circuit
# breaker state.
#
# --proxy-headers is deliberately absent. It would let uvicorn rewrite
# request.client.host from X-Forwarded-For before the rate limiter sees it,
# turning the limiter's untrusted fallback into attacker-controlled input.
# api/middleware.py owns that trust decision.
CMD ["uvicorn", "veilix.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--no-server-header", \
     "--no-access-log"]
