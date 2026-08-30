#!/usr/bin/env python3
"""Generate credentials for Veilix configuration.

    python scripts/hash_secret.py --admin-password        # prompts, no echo
    python scripts/hash_secret.py --api-key               # generates a new key
    python scripts/hash_secret.py --secret                # random hex secret

Two credential types are treated differently on purpose:

**API keys** are generated here with 256 bits of entropy, so they are not
guessable and a fast SHA-256 digest is the correct way to store them. Slow
hashing would add tens of milliseconds of CPU to every authenticated request
for no security gain — there is no dictionary to defend a random 256-bit value
against, and the cost becomes a denial-of-service lever an attacker pulls for
free by sending garbage keys.

**The admin password** is chosen by a human, so it has low entropy and is worth
cracking offline if the hash leaks. It gets Argon2id.

Run from the repository root. Requires the API dependencies to be importable
(the project venv, or inside the api container).
"""

from __future__ import annotations

import argparse
import getpass
import secrets
import sys


def _emit(label: str, value: str, env_var: str) -> None:
    print(f"\n{label}:\n  {value}\n")
    print(f"Add to .env:\n  {env_var}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate Veilix credentials.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--admin-password",
        action="store_true",
        help="Prompt for an admin password and print its Argon2id hash.",
    )
    group.add_argument(
        "--api-key",
        action="store_true",
        help="Generate an API key and print its SHA-256 digest.",
    )
    group.add_argument(
        "--secret",
        action="store_true",
        help="Generate a 32-byte random hex secret.",
    )
    args = parser.parse_args()

    if args.secret:
        _emit("Random secret", secrets.token_hex(32), "SEARXNG_SECRET")
        return 0

    try:
        from veilix.core.security import generate_api_key, hash_api_key, hash_password
    except ImportError:
        print(
            "Could not import veilix. Install the API package first:\n"
            "  pip install -e apps/api\n"
            "or run this inside the api container:\n"
            "  docker compose exec api python /app/scripts/hash_secret.py --api-key",
            file=sys.stderr,
        )
        return 1

    if args.api_key:
        key = generate_api_key()
        print(f"\nAPI key (give this to the client, it is not recoverable later):\n  {key}")
        _emit("SHA-256 digest", hash_api_key(key), "VEILIX_API_KEY_HASHES")
        print("For several keys, comma-separate the digests.\n")
        return 0

    # --admin-password
    password = getpass.getpass("Admin password: ")
    if len(password) < 12:
        # Argon2 raises the cost of cracking; it does not rescue a weak
        # password. Length is what actually decides that fight.
        print("Refusing: use at least 12 characters.", file=sys.stderr)
        return 1
    if password != getpass.getpass("Confirm: "):
        print("Passwords did not match.", file=sys.stderr)
        return 1

    _emit("Argon2id hash", hash_password(password), "VEILIX_ADMIN_PASSWORD_HASH")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
