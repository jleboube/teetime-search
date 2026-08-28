#!/usr/bin/env python3
"""Proactive tee time watcher.

Runs unattended (launchd fires it daily at the time in prefs) and does what
the user would do by hand: for each usual play day that has come inside the
booking window, search the connected platforms and — only when something new
appeared — send an iMessage digest with the best openings. No Claude session,
no tokens, no browser: just the local service and the keychain.

Design decisions that matter:

- **Anchored to booking windows, not the calendar.** A play day is checked
  once it is within `lead_days` of today — the morning the tee sheet opens is
  when the good slots exist. Checking daily after that catches cancellations.
- **Quiet by default.** A snapshot of seen tee times is kept per date; a
  message goes out only on the first sighting or when new times appear.
  A watcher that texts "still nothing" every morning gets muted by Friday.
- **Gentle on the platforms.** One search per watched date per run, once a
  day. This is indistinguishable from the user checking manually. Do not
  wire this into a tight loop; that is how member accounts get suspended.
- **iMessage via Messages.app itself** (osascript), so delivery uses the
  user's own account to their own recipient list. First send may trigger a
  macOS Automation permission prompt — approve it once. Falls back to a
  macOS notification when no recipient is configured or the send fails.

Usage:
    python scripts/watch.py --dry-run          print what would be sent
    python scripts/watch.py                    real run (what launchd calls)
    python scripts/watch.py --test-message     send a test iMessage now
    python scripts/watch.py --install-launchd  schedule the daily run
    python scripts/watch.py --uninstall-launchd
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import _bootstrap  # noqa: F401  (re-execs under the managed venv if needed)
import httpx

import creds
import prefs as prefs_mod

SERVICE_URL = "http://127.0.0.1:8077"
STATE_DIR = prefs_mod.CONFIG_DIR / "state"
LOG_DIR = prefs_mod.CONFIG_DIR / "logs"
PLIST_PATH = Path("~/Library/LaunchAgents/com.teetime.watcher.plist").expanduser()
SKILL_DIR = Path(__file__).resolve().parent.parent

DAY_INDEX = {d: i for i, d in enumerate(prefs_mod.DAYS)}

SEND_APPLESCRIPT = """
on run {msg, target}
    tell application "Messages"
        set svc to 1st account whose service type = iMessage
        send msg to participant target of svc
    end tell
end run
"""


def log(msg: str) -> None:
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}")


def target_dates(p: dict, today: date) -> list[date]:
    """Every occurrence of a usual play day inside the booking window."""
    out = []
    horizon = int(p.get("lead_days", 7))
    for day in p.get("play_days", []):
        idx = DAY_INDEX.get(day)
        if idx is None:
            continue
        first = today + timedelta(days=(idx - today.weekday()) % 7)
        d = first
        while (d - today).days <= horizon:
            out.append(d)
            d += timedelta(days=7)
    return sorted(set(out))


def ensure_service() -> bool:
    """Health-check the aggregator; start the native service if it's down.
    Fall back to Docker for installs that use the compose stack."""
    import serve

    if serve.healthy():
        return True
    log("service down, starting native service")
    if serve.start() == 0:
        return True
    log("native start failed, trying docker compose")
    subprocess.run(
        ["docker", "compose", "-f",
         str(SKILL_DIR / "service" / "docker-compose.yml"), "up", "-d"],
        capture_output=True, timeout=120, check=False,
    )
    time.sleep(8)
    try:
        return httpx.get(f"{SERVICE_URL}/health", timeout=3.0).status_code == 200
    except httpx.HTTPError:
        return False


def run_search(p: dict, when: date) -> dict | None:
    start, _, end = p["window"].partition("-")
    configs = creds.load_all()
    if p.get("demo"):
        configs["demo"] = {"enabled": True}
    if not configs:
        log("no platforms connected and demo off — nothing to watch")
        return None
    body = {
        "zip_code": p["origin"],
        "date": when.isoformat(),
        "players": int(p["players"]),
        "window_start": start.strip(),
        "window_end": end.strip(),
        "max_radius_mi": float(p["max_radius_mi"]),
        "provider_configs": configs,
    }
    resp = httpx.post(f"{SERVICE_URL}/search", json=body, timeout=30.0)
    if resp.status_code != 200:
        log(f"search for {when} failed: {resp.status_code} {resp.text[:200]}")
        return None
    return resp.json()


def flatten(payload: dict) -> list[dict]:
    """Course groups in band order, tagged with their band key."""
    out = []
    for key in ["5mi", "10mi", "15mi", "20mi", "25mi", "35mi"]:
        for g in payload.get("bands", {}).get(key, []):
            out.append(g)
    return out


def listing_ids(groups: list[dict]) -> set[str]:
    return {
        f"{g['canonical_id']}|{x['tee_off']}"
        for g in groups
        for x in g["listings"]
    }


def fmt_time(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%-I:%M%p").lower().replace("m", "")


def build_message(p: dict, when: date, payload: dict, fresh: int, first: bool) -> str:
    groups = flatten(payload)
    day = when.strftime("%a %b %-d")
    what = "tee sheet is open" if first else f"{fresh} new tee times"
    lines = [f"⛳ {day}: {what} — {len(groups)} courses near {p['origin']} "
             f"for {p['players']}"]
    for g in groups[:4]:
        best = min(
            g["listings"],
            key=lambda x: x["price_per_player"]
            if x["price_per_player"] is not None else 1e9,
        )
        price = (f"${best['price_per_player']:.0f}"
                 if best["price_per_player"] is not None else "$?")
        lines.append(
            f"• {fmt_time(best['tee_off'])} {g['name']} {price} "
            f"({g['distance_mi']:.0f} mi)"
        )
    if len(groups) > 4:
        lines.append(f"…and {len(groups) - 4} more")
    failed = [x for x in payload.get("providers", []) if not x["ok"]]
    if failed:
        lines.append("⚠ partial: no answer from "
                     + ", ".join(x["provider"] for x in failed))
    if p.get("demo"):
        lines.append("(demo data — fictional)")
    lines.append("Ask Claude for the full tee sheet to book.")
    return "\n".join(lines)


def send_imessage(msg: str, to: str) -> bool:
    r = subprocess.run(
        ["osascript", "-e", SEND_APPLESCRIPT, msg, to],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        log(f"iMessage send failed: {r.stderr.strip()[:200]}")
        return False
    return True


def notify(msg: str) -> None:
    first = msg.splitlines()[0].replace('"', "'")
    subprocess.run(
        ["osascript", "-e",
         f'display notification "{first}" with title "Tee Times"'],
        capture_output=True, timeout=10, check=False,
    )


def deliver(msg: str, p: dict, dry: bool) -> None:
    if dry:
        print("--- would send " + ("via iMessage to " + p["imessage_to"]
                                   if p.get("imessage_to") else "as notification"))
        print(msg)
        print("---")
        return
    if p.get("imessage_to") and send_imessage(msg, p["imessage_to"]):
        log(f"sent iMessage to {p['imessage_to']}")
        return
    notify(msg)
    log("delivered as macOS notification")


def check(dry: bool, only: date | None) -> int:
    p = prefs_mod.load()
    if not p:
        log("no preferences — run: python scripts/prefs.py init")
        return 1
    if not ensure_service():
        log("aggregator service unreachable; skipping this run")
        return 1

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    dates = [only] if only else target_dates(p, date.today())
    if not dates:
        log("no watched play days inside the booking window today")
        return 0

    for when in dates:
        payload = run_search(p, when)
        if payload is None:
            continue
        groups = flatten(payload)
        ids = listing_ids(groups)

        snap = STATE_DIR / f"{when.isoformat()}.json"
        prev: set[str] = set(json.loads(snap.read_text())) if snap.exists() else set()
        fresh = ids - prev
        first = not snap.exists()
        if not dry:
            snap.write_text(json.dumps(sorted(ids)))

        if not groups:
            log(f"{when}: nothing open in window; staying quiet")
        elif first or fresh:
            deliver(build_message(p, when, payload, len(fresh), first), p, dry)
        else:
            log(f"{when}: no new tee times since last check; staying quiet")

    # Old snapshots are worthless the day after; don't accumulate them.
    for f in STATE_DIR.glob("*.json"):
        try:
            if date.fromisoformat(f.stem) < date.today():
                f.unlink()
        except ValueError:
            pass
    return 0


def install_launchd() -> int:
    p = prefs_mod.load()
    if not p:
        print("run prefs.py init first", file=sys.stderr)
        return 1
    hour, _, minute = p.get("run_at", "07:00").partition(":")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile = LOG_DIR / "watch.log"
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.teetime.watcher</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>{Path(__file__).resolve()}</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>{int(hour)}</integer>
        <key>Minute</key><integer>{int(minute or 0)}</integer>
    </dict>
    <key>StandardOutPath</key><string>{logfile}</string>
    <key>StandardErrorPath</key><string>{logfile}</string>
</dict>
</plist>
"""
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    if PLIST_PATH.exists():
        subprocess.run(["launchctl", "unload", str(PLIST_PATH)],
                       capture_output=True, check=False)
    PLIST_PATH.write_text(plist)
    r = subprocess.run(["launchctl", "load", "-w", str(PLIST_PATH)],
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        print(f"launchctl load failed: {r.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"Watcher scheduled daily at {p.get('run_at', '07:00')} "
          f"({PLIST_PATH.name}); log: {logfile}")
    return 0


def uninstall_launchd() -> int:
    if not PLIST_PATH.exists():
        print("watcher is not installed")
        return 0
    subprocess.run(["launchctl", "unload", str(PLIST_PATH)],
                   capture_output=True, check=False)
    PLIST_PATH.unlink()
    print("Watcher unscheduled.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="print instead of sending; don't update snapshots")
    ap.add_argument("--date", help="check one specific date (YYYY-MM-DD)")
    ap.add_argument("--test-message", action="store_true",
                    help="send a test iMessage to the configured recipient")
    ap.add_argument("--install-launchd", action="store_true")
    ap.add_argument("--uninstall-launchd", action="store_true")
    args = ap.parse_args()

    if args.install_launchd:
        return install_launchd()
    if args.uninstall_launchd:
        return uninstall_launchd()
    if args.test_message:
        p = prefs_mod.load()
        if not p.get("imessage_to"):
            print("no imessage_to configured in prefs", file=sys.stderr)
            return 1
        ok = send_imessage("⛳ Tee time watcher test — delivery works.",
                           p["imessage_to"])
        print("sent" if ok else "failed")
        return 0 if ok else 1

    only = date.fromisoformat(args.date) if args.date else None
    return check(args.dry_run, only)


if __name__ == "__main__":
    raise SystemExit(main())
