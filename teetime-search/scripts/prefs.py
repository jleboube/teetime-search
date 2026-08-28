#!/usr/bin/env python3
"""Play preferences for the tee time watcher.

Stores the user's usual golf pattern — which days they play, how many are in
the group, where, and how they want to be told — so the watcher can search on
their behalf without being asked. Preferences are not secrets: they live in
plain JSON at ~/.config/teetime/prefs.json, deliberately outside the skill
folder so a skill update never wipes them. Credentials stay in the keychain.

Usage:
    python scripts/prefs.py init                       interactive interview
    python scripts/prefs.py init --days sat,sun --players 4 --origin 47714 \
        --window 07:00-11:00 --imessage-to +15551234567
    python scripts/prefs.py show
    python scripts/prefs.py path
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CONFIG_DIR = Path("~/.config/teetime").expanduser()
PREFS_PATH = CONFIG_DIR / "prefs.json"

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

DEFAULTS = {
    "play_days": ["sat"],
    "window": "06:30-12:00",
    "players": 4,
    "origin": "",            # 5-digit ZIP
    "max_radius_mi": 35.0,
    # How far ahead courses open their booking window. The watcher checks a
    # play day as soon as it comes inside this horizon — that's the morning
    # the good slots appear, not three days later.
    "lead_days": 7,
    # iMessage recipient: a phone number or Apple ID email. Your own number
    # sends to your self-thread. Empty = macOS notification only.
    "imessage_to": "",
    # When the daily watcher run fires (used by watch.py --install-launchd).
    "run_at": "07:00",
    # True only for trying the watcher before any platform is connected.
    "demo": False,
}


def load() -> dict:
    if not PREFS_PATH.exists():
        return {}
    return json.loads(PREFS_PATH.read_text())


def save(prefs: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PREFS_PATH.write_text(json.dumps(prefs, indent=2) + "\n")


def parse_days(raw: str) -> list[str]:
    days = [d.strip().lower()[:3] for d in raw.split(",") if d.strip()]
    bad = [d for d in days if d not in DAYS]
    if bad:
        raise SystemExit(f"unknown day(s): {', '.join(bad)} — use {'/'.join(DAYS)}")
    return days


def cmd_init(args: argparse.Namespace) -> int:
    prefs = {**DEFAULTS, **load()}

    flag_values = {
        "play_days": parse_days(args.days) if args.days else None,
        "window": args.window,
        "players": args.players,
        "origin": args.origin,
        "max_radius_mi": args.max_radius,
        "lead_days": args.lead_days,
        "imessage_to": args.imessage_to,
        "run_at": args.run_at,
        "demo": args.demo,
    }
    provided = {k: v for k, v in flag_values.items() if v is not None}

    if provided:
        # Non-interactive: flags only. This is the path Claude uses after
        # collecting the answers conversationally.
        prefs.update(provided)
    else:
        print("Tee time watcher setup — enter to keep [current] value.\n")
        raw = input(f"Days you usually play (e.g. sat,sun) [{','.join(prefs['play_days'])}]: ").strip()
        if raw:
            prefs["play_days"] = parse_days(raw)
        raw = input(f"Usual tee-off window [{prefs['window']}]: ").strip()
        if raw:
            prefs["window"] = raw
        raw = input(f"Usual group size (1-4) [{prefs['players']}]: ").strip()
        if raw:
            prefs["players"] = int(raw)
        raw = input(f"Home ZIP [{prefs['origin'] or 'required'}]: ").strip()
        if raw:
            prefs["origin"] = raw
        raw = input(f"Search radius in miles [{prefs['max_radius_mi']:.0f}]: ").strip()
        if raw:
            prefs["max_radius_mi"] = float(raw)
        raw = input(f"Days ahead your courses open booking [{prefs['lead_days']}]: ").strip()
        if raw:
            prefs["lead_days"] = int(raw)
        raw = input(
            "iMessage recipient — your phone or Apple ID email; your own number\n"
            f"texts your self-thread; blank = macOS notification only [{prefs['imessage_to'] or 'none'}]: "
        ).strip()
        if raw:
            prefs["imessage_to"] = raw
        raw = input(f"Daily check time (24h HH:MM) [{prefs['run_at']}]: ").strip()
        if raw:
            prefs["run_at"] = raw

    if not prefs.get("origin"):
        print("origin (home ZIP) is required", file=sys.stderr)
        return 1
    if "-" not in prefs["window"]:
        print("window must look like 07:00-11:00", file=sys.stderr)
        return 1

    save(prefs)
    print(f"Saved {PREFS_PATH}")
    print("Next: python scripts/watch.py --dry-run   (see what a check would send)")
    print("Then: python scripts/watch.py --install-launchd   (schedule it)")
    return 0


def cmd_show() -> int:
    prefs = load()
    if not prefs:
        print("No preferences yet. Run: python scripts/prefs.py init")
        return 1
    print(json.dumps(prefs, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="create or update preferences")
    p_init.add_argument("--days", help="comma list: mon..sun")
    p_init.add_argument("--window", help="e.g. 07:00-11:00")
    p_init.add_argument("--players", type=int, choices=[1, 2, 3, 4])
    p_init.add_argument("--origin", help="5-digit home ZIP")
    p_init.add_argument("--max-radius", type=float, dest="max_radius")
    p_init.add_argument("--lead-days", type=int, dest="lead_days")
    p_init.add_argument("--imessage-to", dest="imessage_to",
                        help="phone/Apple ID email; '' for notification-only")
    p_init.add_argument("--run-at", dest="run_at", help="daily check time HH:MM")
    p_init.add_argument("--demo", action="store_true", default=None,
                        help="watch demo inventory (for trying it out)")
    p_init.add_argument("--no-demo", action="store_false", dest="demo")

    sub.add_parser("show", help="print current preferences")
    sub.add_parser("path", help="print the prefs file path")

    args = ap.parse_args()
    if args.cmd == "init":
        return cmd_init(args)
    if args.cmd == "show":
        return cmd_show()
    if args.cmd == "path":
        print(PREFS_PATH)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
