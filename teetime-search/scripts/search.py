#!/usr/bin/env python3
"""Tee time search CLI.

Runs on the host. Pulls tier-2 credentials from the OS keychain, posts a search
to the loopback-bound aggregator service, and prints results banded by distance.

    python scripts/search.py --origin 47714 --date 2026-08-29 --players 4
    python scripts/search.py --origin 37.77,-122.41 --date today --window 07:00-11:00
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime, timedelta

import httpx

import creds

SERVICE_URL = "http://127.0.0.1:8077"
BAND_LABELS = {
    "5mi": "Within 5 miles",
    "10mi": "Within 10 miles",
    "15mi": "Within 15 miles",
    "20mi": "Within 20 miles",
    "25mi": "Within 25 miles",
    "35mi": "Within 35 miles",
}


def parse_date(value: str) -> str:
    if value == "today":
        return date.today().isoformat()
    if value == "tomorrow":
        return (date.today() + timedelta(days=1)).isoformat()
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise SystemExit(f"bad date {value!r}; use YYYY-MM-DD, today, or tomorrow")


def parse_origin(value: str) -> dict:
    if "," in value:
        lat, _, lng = value.partition(",")
        try:
            return {"lat": float(lat), "lng": float(lng)}
        except ValueError:
            raise SystemExit(f"bad lat,lng {value!r}")
    if value.isdigit() and len(value) == 5:
        return {"zip_code": value}
    raise SystemExit(f"origin must be a 5-digit ZIP or lat,lng — got {value!r}")


def parse_window(value: str | None) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    if "-" not in value:
        raise SystemExit("window must look like 07:00-11:00")
    start, _, end = value.partition("-")
    return start.strip(), end.strip()


def fmt_time(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    return dt.strftime("%-I:%M%p").lower().replace("m", "")


def best_listing(group: dict) -> tuple[dict, str]:
    """Cheapest listing for a course group, plus the inline note about
    alternatives and price confidence."""
    listings = group["listings"]
    best = min(
        listings,
        key=lambda x: x["price_per_player"]
        if x["price_per_player"] is not None
        else 1e9,
    )
    note = ""
    # Same course on multiple platforms: show the alternative, since the
    # user may hold rewards or a membership on the pricier one.
    others = {
        x["provider"]: x["price_per_player"]
        for x in listings
        if x["provider"] != best["provider"] and x["price_per_player"] is not None
    }
    if others:
        note = "  (also " + ", ".join(f"${v:.0f} on {k}" for k, v in others.items()) + ")"
    if best.get("price_confidence") == "low":
        note += "  [price approx]"
    return best, note


def fmt_price(listing: dict) -> str:
    if listing["price_per_player"] is None:
        return "  —"
    return f"${listing['price_per_player']:.0f}"


def render(payload: dict) -> str:
    bands = payload.get("bands", {})
    # Coverage failures must show even when there are no results — "no tee
    # times" and "nothing was searched" are very different answers.
    failed = [p for p in payload.get("providers", []) if not p["ok"]]
    if not bands:
        msg = "No tee times found in that radius."
        if failed:
            detail = ", ".join(f"{p['provider']} ({p['error']})" for p in failed)
            msg += f"\nNote: {detail}"
        return msg

    lines: list[str] = []
    for key, label in BAND_LABELS.items():
        groups = bands.get(key)
        if not groups:
            continue
        lines.append(f"\n{label}")
        for group in groups:
            best, note = best_listing(group)
            source = best["provider"]
            if best.get("member_rate"):
                source += " (your account)"
            lines.append(
                f"  {fmt_time(best['tee_off']):>8}  "
                f"{group['name'][:32]:<34}{fmt_price(best):>5}  "
                f"{best['slots_available']} slots  {source}{note}"
            )

    # Coverage honesty: a golfer who thinks the list is complete when it isn't
    # will book the wrong thing.
    if failed:
        detail = ", ".join(f"{p['provider']} ({p['error']})" for p in failed)
        lines.append(f"\nIncomplete — these providers did not respond: {detail}")

    return "\n".join(lines)


# --- ASCII tee sheet -------------------------------------------------------
# The default human-facing render. If this thing is going to live in a
# terminal, it should look like it belongs there: a range map plotting every
# course by real bearing and distance, then a scorecard per band. Pure ASCII
# (no box-drawing Unicode) so it survives any terminal, pager, or text message.

MAP_HALF_ROWS = 10   # rows above/below the origin
RING_RADII = [5, 15, 25, 35]  # alternating bands keep the rings legible

FLAG = [
    "     |\\",
    "     | \\__",
    "     |_|__\\",
    "     |",
    "  ___|___",
]
FLAG_W = 14  # pad flag rows to a common width so header lines align


def _course_points(payload: dict) -> list[tuple[str, dict, dict]]:
    """(letter, group, best listing) in band order — the letter is the shared
    key between the map and the scorecard."""
    points = []
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # I and O read as 1 and 0 on a map
    i = 0
    for key in BAND_LABELS:
        for group in payload.get("bands", {}).get(key, []):
            if i >= len(letters):
                return points
            best, _ = best_listing(group)
            points.append((letters[i], group, best))
            i += 1
    return points


def render_map(payload: dict, points: list, max_r: float) -> list[str]:
    olat, olng = payload["origin_lat"], payload["origin_lng"]
    mile_per_row = max_r / MAP_HALF_ROWS
    mile_per_col = mile_per_row / 2  # terminal cells are ~2x taller than wide
    half_cols = int(max_r / mile_per_col)

    height = MAP_HALF_ROWS * 2 + 1
    width = half_cols * 2 + 1 + 3  # margin so the outer ring's label fits
    cy, cx = MAP_HALF_ROWS, half_cols
    grid = [[" "] * width for _ in range(height)]

    # Distance rings, swept parametrically so they stay round-ish despite the
    # cell aspect ratio.
    for r in RING_RADII:
        if r > max_r:
            continue
        for deg in range(0, 360, 2):
            a = math.radians(deg)
            col = cx + int(round(r * math.sin(a) / mile_per_col))
            row = cy - int(round(r * math.cos(a) / mile_per_row))
            if 0 <= row < height and 0 <= col < width:
                grid[row][col] = "."
        # Label each ring on the east axis.
        lc = cx + int(round(r / mile_per_col))
        label = str(r)
        if lc + len(label) < width:
            for j, ch in enumerate(label):
                grid[cy][lc + j] = ch

    # Courses, plotted by real offset from the origin.
    for letter, group, _ in points:
        dx = (group["lng"] - olng) * 69.0 * math.cos(math.radians(olat))
        dy = (group["lat"] - olat) * 69.0
        col = cx + int(round(dx / mile_per_col))
        row = cy - int(round(dy / mile_per_row))
        if 0 <= row < height and 0 <= col < width:
            grid[row][col] = letter

    grid[cy][cx] = "@"  # you are here

    out = ["".join(r).rstrip() for r in grid]
    out.append("@ you   . rings at " + "/".join(
        str(r) for r in RING_RADII if r <= max_r) + " mi   letters = courses below")
    return out


def render_ascii(payload: dict, meta: dict) -> str:
    failed = [p for p in payload.get("providers", []) if not p["ok"]]
    points = _course_points(payload)

    if not points:
        msg = "No tee times found in that radius."
        if failed:
            detail = ", ".join(f"{p['provider']} ({p['error']})" for p in failed)
            msg += f"\nNote: {detail}"
        return msg

    total_slots = sum(
        x["slots_available"] for _, g, _ in points for x in g["listings"]
    )
    max_r = float(meta.get("max_radius_mi", 35.0))
    inner = 66  # scorecard interior width

    day = date.fromisoformat(meta["date"]).strftime("%a %b %-d")
    head1 = f"T E E   S H E E T   .   {day}   .   {meta['players']} players   .   {meta['origin']}"
    head2 = (
        f"{len(points)} courses . {total_slots} open seats . within {max_r:.0f} mi"
    )

    lines: list[str] = []
    lines.append(FLAG[0])
    lines.append(f"{FLAG[1]:<{FLAG_W}}" + head1)
    lines.append(f"{FLAG[2]:<{FLAG_W}}" + head2)
    lines.append(FLAG[3])
    lines.append(f"{FLAG[4]:<{FLAG_W}}".rstrip("_ ") + "_" * (len(head1) + 5))
    lines.append("")
    lines.extend("   " + row for row in render_map(payload, points, max_r))
    lines.append("")

    # Scorecard, one section per band.
    idx = 0
    for key, label in BAND_LABELS.items():
        groups = payload.get("bands", {}).get(key, [])
        if not groups:
            continue
        title = f" {label} "
        lines.append("+--" + title + "-" * max(inner - len(title) - 2, 0) + "+")
        for group in groups:
            if idx >= len(points):
                break
            letter, _, best = points[idx]
            idx += 1
            _, note = best_listing(group)
            src = best["provider"] + (" *" if best.get("member_rate") else "")
            row = (
                f" {letter}  {fmt_time(best['tee_off']):>7}  "
                f"{group['name'][:28]:<29} {fmt_price(best):>5}  "
                f"{best['slots_available']} slots  {src[:12]}"
            )
            lines.append("|" + f"{row:<{inner}}"[:inner] + "|")
            if note:
                lines.append("|" + f"      {note.strip():<{inner - 6}}"[:inner] + "|")
        lines.append("+" + "-" * inner + "+")

    footnotes = []
    if any(best.get("member_rate") for _, _, best in points):
        footnotes.append("* your account")
    if any(best["provider"] == "demo" for _, _, best in points):
        footnotes.append("DEMO DATA (fictional inventory)")
    if failed:
        footnotes.append(
            "INCOMPLETE — no answer from: "
            + ", ".join(f"{p['provider']} ({p['error']})" for p in failed)
        )
    lines.append("  " + "   ".join(footnotes))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--origin", required=True, help="5-digit ZIP or lat,lng")
    ap.add_argument("--date", default="today")
    ap.add_argument("--players", type=int, default=4, choices=[1, 2, 3, 4])
    ap.add_argument("--window", help="e.g. 07:00-11:00")
    ap.add_argument("--max-radius", type=float, default=35.0)
    ap.add_argument("--holes", type=int, choices=[9, 18])
    ap.add_argument("--json", action="store_true", help="raw output")
    ap.add_argument(
        "--plain", action="store_true", help="compact table, no map"
    )
    ap.add_argument(
        "--demo",
        action="store_true",
        help="include the synthetic demo provider (fictional inventory)",
    )
    args = ap.parse_args()

    window_start, window_end = parse_window(args.window)
    provider_configs = creds.load_all()
    if args.demo:
        # Explicit opt-in only: synthetic data must never mix silently into a
        # real search.
        provider_configs["demo"] = {"enabled": True}
    body = {
        **parse_origin(args.origin),
        "date": parse_date(args.date),
        "players": args.players,
        "window_start": window_start,
        "window_end": window_end,
        "max_radius_mi": args.max_radius,
        "holes": args.holes,
        # Read at invocation, held only for this request.
        "provider_configs": provider_configs,
    }

    try:
        resp = httpx.post(f"{SERVICE_URL}/search", json=body, timeout=30.0)
    except httpx.ConnectError:
        print(
            "Cannot reach the aggregator service. Start it with:\n"
            "    cd service && docker compose up -d",
            file=sys.stderr,
        )
        return 1

    if resp.status_code != 200:
        print(f"search failed ({resp.status_code}): {resp.text}", file=sys.stderr)
        return 1

    payload = resp.json()
    if args.json:
        print(json.dumps(payload, indent=2))
    elif args.plain:
        print(render(payload))
    else:
        meta = {
            "date": body["date"],
            "players": args.players,
            "origin": args.origin,
            "max_radius_mi": args.max_radius,
        }
        print(render_ascii(payload, meta))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
