#!/usr/bin/env python3
"""Credential broker for tier-2 providers.

Credentials live in the operating system's keychain — macOS Keychain,
libsecret on Linux, Windows Credential Manager — and are read into memory only
at the moment a search runs. They are never written to a file, never baked into
a container image, never placed in docker-compose.yml, and never sent to any
host but the loopback-bound local service.

This script runs on the host, outside Docker, because containers cannot reach
the OS keychain. That constraint is the whole reason the architecture splits
here: the broker stays native, the adapters stay containerized.

Usage:
    python scripts/creds.py set chronogolf
    python scripts/creds.py list
    python scripts/creds.py rm chronogolf
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys

import _bootstrap  # noqa: F401  (re-execs under the managed venv if needed)

# Soft import: search.py imports this module for load_all(), and a search with
# no tier-2 credentials (including --demo mode) must work on a machine that has
# never installed keyring. Only the credential-management commands require it.
try:
    import keyring
    from keyring.errors import KeyringError
except ImportError:  # pragma: no cover
    keyring = None


def _require_keyring() -> None:
    if keyring is None:
        print(
            "keyring is not installed. Install it with:\n"
            "    pip install keyring\n"
            "It is intentionally not vendored into the container — credential "
            "storage stays on the host.",
            file=sys.stderr,
        )
        raise SystemExit(1)

SERVICE = "teetime-search"
INDEX_KEY = "__configured_providers__"

# What each provider connection needs. Keeping this explicit means the prompt
# asks for exactly the right fields instead of a generic username/password
# guess. Fields ending in _ids or _zips accept comma-separated lists.
#
# course_zips exists because most booking APIs don't return course coordinates,
# and a listing without coordinates can't be distance-banded. The user knows
# their own club's ZIP; asking for it once beats geocoding by name.
PROVIDER_FIELDS: dict[str, list[tuple[str, bool, bool]]] = {
    # provider: [(field_name, is_secret, required), ...]
    "chronogolf": [
        ("access_token", True, True),
        ("club_ids", False, True),
    ],
    "foreup": [
        ("username", False, True),
        ("password", True, True),
        # From the club's booking URL: foreupsoftware.com/index.php/booking/
        # {course_id}/{schedule_id}
        ("course_id", False, True),
        ("schedule_ids", False, True),
        ("course_zips", False, True),
        ("booking_class_id", False, False),
    ],
    "teesnap": [
        # The club's subdomain: {subdomain}.teesnap.net
        ("subdomain", False, True),
        ("username", False, True),
        ("password", True, True),
        ("course_zip", False, True),
    ],
}

CONSENT = """
Before storing credentials, understand what you are agreeing to:

  * Automated access to a booking platform may violate its terms of service.
    Account suspension is a real possibility, and the risk is yours.
  * These credentials are stored in your OS keychain on this machine only.
    They are not uploaded anywhere and no one else can retrieve them.
  * If the account has a saved payment method, treat it accordingly.

This tool never completes a booking. It reads availability only.
"""


def _index() -> list[str]:
    raw = keyring.get_password(SERVICE, INDEX_KEY)
    return json.loads(raw) if raw else []


def _set_index(names: list[str]) -> None:
    keyring.set_password(SERVICE, INDEX_KEY, json.dumps(sorted(set(names))))


def cmd_set(provider: str) -> int:
    _require_keyring()
    fields = PROVIDER_FIELDS.get(provider)
    if fields is None:
        print(
            f"Unknown provider {provider!r}. Known: "
            f"{', '.join(sorted(PROVIDER_FIELDS))}",
            file=sys.stderr,
        )
        return 1

    print(CONSENT)
    if input("Continue? [y/N] ").strip().lower() not in {"y", "yes"}:
        print("Aborted. Nothing stored.")
        return 0

    payload: dict[str, object] = {}
    for field, secret, required in fields:
        prompt = f"{provider} {field}" + ("" if required else " (optional)")
        if secret:
            value = getpass.getpass(f"{prompt} (hidden): ")
        else:
            value = input(f"{prompt}: ").strip()
        if not value:
            if required:
                print(f"{field} is required.", file=sys.stderr)
                return 1
            continue
        # Comma-separated lists become real lists so adapters don't have to
        # re-parse them at request time.
        payload[field] = (
            [v.strip() for v in value.split(",") if v.strip()]
            if field.endswith(("_ids", "_zips"))
            else value
        )

    keyring.set_password(SERVICE, provider, json.dumps(payload))
    _set_index(_index() + [provider])
    print(f"Stored {provider} credentials in the OS keychain.")
    return 0


def cmd_list() -> int:
    _require_keyring()
    configured = _index()
    if not configured:
        print("No tier-2 providers configured. Public search still works.")
        return 0
    print("Configured providers:")
    for name in configured:
        print(f"  {name}")
    return 0


def cmd_rm(provider: str) -> int:
    _require_keyring()
    try:
        keyring.delete_password(SERVICE, provider)
    except KeyringError:
        print(f"No stored credentials for {provider}.", file=sys.stderr)
        return 1
    _set_index([n for n in _index() if n != provider])
    print(f"Removed {provider} credentials.")
    return 0


def load_all() -> dict[str, dict]:
    """Read every configured provider's config. Called by search.py at
    request time; the result is held in memory for one request only."""
    configs: dict[str, dict] = {}
    if keyring is None:
        return configs
    for name in _index():
        raw = keyring.get_password(SERVICE, name)
        if raw:
            configs[name] = json.loads(raw)
    return configs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_set = sub.add_parser("set", help="store credentials for a provider")
    p_set.add_argument("provider")
    sub.add_parser("list", help="show configured providers")
    p_rm = sub.add_parser("rm", help="remove a provider's credentials")
    p_rm.add_argument("provider")

    args = ap.parse_args()
    if args.cmd == "set":
        return cmd_set(args.provider)
    if args.cmd == "list":
        return cmd_list()
    if args.cmd == "rm":
        return cmd_rm(args.provider)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
