#!/usr/bin/env python3
"""Build the offline ZIP centroid database.

Source is the Census Bureau ZCTA gazetteer — a public-domain file of roughly
33,000 ZIP Code Tabulation Areas with centroid coordinates. Baking it into the
image means geo resolution needs no API key, has no rate limit, and never tells
a third party where anyone is searching.

Run at image build time:
    python scripts/build_zip_db.py --out app/data/zips.sqlite

ZCTAs are not identical to USPS ZIP codes — a handful of PO-box-only ZIPs have
no ZCTA. For picking a search origin, centroid accuracy of a few hundred metres
is irrelevant against a 5-mile inner band, so the tradeoff is worth the
simplicity of having no external dependency.
"""
from __future__ import annotations

import argparse
import csv
import io
import sqlite3
import sys
import urllib.request
import zipfile
from pathlib import Path

GAZETTEER_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/"
    "2023_Gaz_zcta_national.zip"
)


def fetch_rows(url: str) -> list[tuple[str, float, float]]:
    with urllib.request.urlopen(url, timeout=120) as resp:
        payload = resp.read()

    rows: list[tuple[str, float, float]] = []
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        name = next(n for n in zf.namelist() if n.lower().endswith(".txt"))
        with zf.open(name) as fh:
            text = io.TextIOWrapper(fh, encoding="latin-1")
            reader = csv.DictReader(text, delimiter="\t")
            for row in reader:
                clean = {k.strip(): (v.strip() if v else v) for k, v in row.items()}
                zcta = clean.get("GEOID")
                lat, lng = clean.get("INTPTLAT"), clean.get("INTPTLONG")
                if not (zcta and lat and lng):
                    continue
                try:
                    rows.append((zcta.zfill(5), float(lat), float(lng)))
                except ValueError:
                    continue
    return rows


def build(rows: list[tuple[str, float, float]], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    conn = sqlite3.connect(out)
    conn.execute(
        "CREATE TABLE zips (zip TEXT PRIMARY KEY, lat REAL NOT NULL, lng REAL NOT NULL)"
    )
    conn.executemany("INSERT OR REPLACE INTO zips VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.execute("VACUUM")
    conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--url", default=GAZETTEER_URL)
    args = ap.parse_args()

    try:
        rows = fetch_rows(args.url)
    except Exception as exc:  # noqa: BLE001
        print(f"failed to fetch gazetteer: {exc}", file=sys.stderr)
        print(
            "If the build host has no network access, fetch the file "
            "separately and pass a file:// URL to --url.",
            file=sys.stderr,
        )
        return 1

    if len(rows) < 25_000:
        print(
            f"only {len(rows)} ZCTAs parsed — the gazetteer format likely "
            "changed. Refusing to build a truncated database.",
            file=sys.stderr,
        )
        return 1

    build(rows, args.out)
    print(f"wrote {len(rows):,} ZIP centroids to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
