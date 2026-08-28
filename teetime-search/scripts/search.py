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


# --- Terminal UI ----------------------------------------------------------
# The default human-facing render, built on rich. One table, real distances,
# section breaks at the band edges. Degrades to the plain table when rich
# isn't installed, and rich itself strips color when output is piped.

BAND_EDGES = {"5mi": 5, "10mi": 10, "15mi": 15, "20mi": 20, "25mi": 25, "35mi": 35}


def render_rich(payload: dict, meta: dict) -> bool:
    """Print the tee sheet. Returns False if rich is unavailable so the
    caller can fall back to the plain render."""
    try:
        from rich import box as rich_box
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
    except ImportError:
        return False

    console = Console()
    bands = payload.get("bands", {})
    failed = [p for p in payload.get("providers", []) if not p["ok"]]

    if not bands:
        console.print("[bold]No tee times found in that radius.[/bold]")
        for p in failed:
            console.print(f"[red]![/red] {p['provider']}: {p['error']}")
        return True

    groups_in_order = [
        (key, g) for key in BAND_LABELS for g in bands.get(key, [])
    ]
    n_seats = sum(
        x["slots_available"] for _, g in groups_in_order for x in g["listings"]
    )

    day = date.fromisoformat(meta["date"]).strftime("%A %b %-d")
    title = Text()
    title.append("\u26f3 Tee Sheet", style="bold green")
    title.append(f"  {day} · {meta['players']} players · near {meta['origin']}")
    sub = Text(
        f"{len(groups_in_order)} courses · {n_seats} open seats · "
        f"within {meta['max_radius_mi']:.0f} mi",
        style="dim",
    )
    console.print(Panel(Text.assemble(title, "\n", sub), box=rich_box.ROUNDED,
                        border_style="green", expand=False))

    table = Table(
        box=rich_box.ROUNDED,
        border_style="dim",
        header_style="bold",
        padding=(0, 1),
        expand=False,
    )
    table.add_column("mi", justify="right", style="dim")
    table.add_column("tee off", justify="right", style="bold cyan")
    table.add_column("course")
    table.add_column("$/player", justify="right")
    table.add_column("slots", justify="center")
    table.add_column("via", style="dim")

    last_band = None
    for key, group in groups_in_order:
        if last_band is not None and key != last_band:
            table.add_section()
        last_band = key

        best, note = best_listing(group)
        course = Text(group["name"], style="bold")
        if note:
            course.append("\n" + note.strip(), style="dim italic")

        price_style = "yellow" if best.get("price_confidence") == "low" else "green"
        price = Text(fmt_price(best), style=price_style)

        via = best["provider"]
        via_text = Text(via, style="yellow" if via == "demo" else "dim")
        if best.get("member_rate"):
            via_text = Text(via + " ·yours", style="magenta")

        table.add_row(
            f"{group['distance_mi']:.1f}",
            fmt_time(best["tee_off"]),
            course,
            price,
            str(best["slots_available"]),
            via_text,
        )

    console.print(table)

    if any(best_listing(g)[0].get("price_confidence") == "low"
           for _, g in groups_in_order):
        console.print("[dim]yellow price = approximate — provider did not state "
                      "per-player-with-cart[/dim]")
    if any(x["provider"] == "demo" for _, g in groups_in_order
           for x in g["listings"]):
        console.print("[yellow bold]DEMO DATA[/yellow bold]"
                      "[yellow] — fictional inventory[/yellow]")
    for p in failed:
        console.print(
            f"[red bold]INCOMPLETE[/red bold] [red]— no answer from "
            f"{p['provider']}: {p['error']}[/red]"
        )
    return True


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
        "--plain", action="store_true", help="compact table, no color/boxes"
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
        if not render_rich(payload, meta):
            print(render(payload))
            print(
                "(tip: pip install rich for the full tee sheet)",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
