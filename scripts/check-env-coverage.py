#!/usr/bin/env python3
"""Fail if the code reads a setting that .env.example does not document.

Guards the Phase 6 bug directly: a variable the code reads but no example
mentions is one an operator will never set, so the feature it controls silently
does nothing. That bug shipped once - three tuning knobs were plumbed through
config, documented in prose, and never reached the container.

Run from the repository root:  python scripts/check-env-coverage.py
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = ROOT / "apps/api/src/veilix/core/config.py"
EXAMPLE = ROOT / ".env.example"

# Settings with no environment variable of their own.
IGNORED_FIELDS = {"model_config"}


def main() -> int:
    config_source = CONFIG.read_text(encoding="utf-8")
    documented = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", EXAMPLE.read_text(encoding="utf-8"), re.M))

    expected: set[str] = set()
    for line in config_source.splitlines():
        # Fields are declared at one level of indentation inside Settings.
        match = re.match(r"^    ([a-z][a-z0-9_]*)\s*:", line)
        if not match or match.group(1) in IGNORED_FIELDS:
            continue

        field = match.group(1)
        # A field with an explicit validation_alias is read by THAT name only -
        # the prefixed form is never consulted. Treating both as required is
        # what made the first version of this check report a variable that
        # cannot exist.
        alias = re.search(rf"{field}\s*:.*?validation_alias=\"([A-Z_]+)\"", config_source, re.S)
        expected.add(alias.group(1) if alias else f"VEILIX_{field.upper()}")

    missing = sorted(name for name in expected if name not in documented)
    if missing:
        print("FAIL: read by config.py but absent from .env.example:")
        for name in missing:
            print(f"  {name}")
        return 1

    print(f"OK: all {len(expected)} settings are documented in .env.example")
    return 0


if __name__ == "__main__":
    sys.exit(main())
