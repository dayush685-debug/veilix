#!/usr/bin/env python3
"""Write a throwaway .env for CI, derived from .env.example.

Derived rather than hand-listed. An earlier version of this enumerated ten
variables inline in the workflow, so the other fifteen never reached the
container, and verify-stack.sh correctly reported that settings the
application defines were not being passed to it.

Starting from the example keeps CI in step with what an operator actually
deploys, and removes a second list that would need keeping in sync.

Secrets generated here are random per run and exist only on the runner.

    python scripts/make-ci-env.py [--api-key-out .ci-api-key]
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import secrets
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def build_env(example: str, values: dict[str, str]) -> str:
    """Apply `values` over the example, appending anything it does not define."""
    out = example
    for name, value in values.items():
        out, count = re.subn(
            rf"^{re.escape(name)}=.*$", f"{name}={value}", out, flags=re.M
        )
        if count == 0:
            out += f"\n{name}={value}\n"
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key-out", default=".ci-api-key")
    args = parser.parse_args()

    try:
        from argon2 import PasswordHasher
    except ImportError:
        print("argon2-cffi is required: pip install argon2-cffi", file=sys.stderr)
        return 1

    api_key = f"vlx_{secrets.token_urlsafe(32)}"

    values = {
        "VEILIX_ENV": "development",
        "SEARXNG_SECRET": secrets.token_hex(32),
        "VEILIX_RATELIMIT_SALT_SEED": secrets.token_hex(32),
        # NOTE: the Argon2 hash is deliberately NOT here. Compose auto-loads
        # .env for variable interpolation, and an Argon2 hash contains $v, $m
        # and $argon2id, which compose expands as variables and blanks out. It
        # goes in .env.secrets instead, which is only ever read as a raw
        # env_file and never interpolated - which is what that overlay is for.
        "VEILIX_API_KEY_HASHES": hashlib.sha256(api_key.encode()).hexdigest(),
        # Required even though the observability profile is never started:
        # `compose config` interpolates every service, profiled or not.
        "GRAFANA_ADMIN_PASSWORD": secrets.token_urlsafe(18),
        # Headroom so the limiter does not throttle the end-to-end suite.
        "VEILIX_RATELIMIT_REQUESTS": "600",
        "VEILIX_PUBLIC_URL": "http://localhost:8088",
    }

    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    (ROOT / ".env").write_text(build_env(example, values), encoding="utf-8")

    # Raw overlay: read verbatim by compose, never interpolated.
    admin_hash = PasswordHasher().hash("ci-throwaway-password")
    (ROOT / ".env.secrets").write_text(
        f"VEILIX_ADMIN_PASSWORD_HASH={admin_hash}\n", encoding="utf-8"
    )
    (ROOT / args.api_key_out).write_text(api_key, encoding="utf-8")

    defined = len(re.findall(r"^[A-Z][A-Z0-9_]*=", example, re.M))
    print(
        f"wrote .env from .env.example ({defined} settings, {len(values)} overridden)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
