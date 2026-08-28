"""Geo resolution and distance banding.

ZIP centroids come from a local SQLite database built from the Census ZCTA
gazetteer (see scripts/build_zip_db.py). Keeping this offline means no
geocoding API key, no rate limit on the hot path, and no third party
accumulating a log of where users are searching.
"""
from __future__ import annotations

import math
import os
import sqlite3
from functools import lru_cache
from pathlib import Path

from .models import CourseGroup, TeeTime

# Docker bakes the db into the image; the no-Docker runner (scripts/serve.py)
# builds it under ~/.config/teetime and points here via the env var.
ZIP_DB = Path(
    os.getenv("TEETIME_ZIP_DB") or Path(__file__).parent / "data" / "zips.sqlite"
)

# The bands the product promises. Ordered; each is a strict superset of the last.
BANDS_MI = [5, 10, 15, 20, 25, 35]

EARTH_RADIUS_MI = 3958.7613


class UnknownZip(ValueError):
    pass


@lru_cache(maxsize=4096)
def zip_to_latlng(zip_code: str) -> tuple[float, float]:
    """Resolve a 5-digit US ZIP to its centroid."""
    zip_code = zip_code.strip()[:5]
    if not zip_code.isdigit() or len(zip_code) != 5:
        raise UnknownZip(f"{zip_code!r} is not a 5-digit US ZIP code")

    with sqlite3.connect(f"file:{ZIP_DB}?mode=ro", uri=True) as conn:
        row = conn.execute(
            "SELECT lat, lng FROM zips WHERE zip = ?", (zip_code,)
        ).fetchone()

    if row is None:
        raise UnknownZip(f"ZIP {zip_code} not found in the gazetteer")
    return row[0], row[1]


def haversine_mi(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in statute miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.asin(math.sqrt(a))


def annotate_distance(
    tee_times: list[TeeTime], origin_lat: float, origin_lng: float
) -> list[TeeTime]:
    for tt in tee_times:
        tt.distance_mi = round(
            haversine_mi(origin_lat, origin_lng, tt.course.lat, tt.course.lng), 1
        )
    return tee_times


def band(groups: list[CourseGroup]) -> dict[str, list[CourseGroup]]:
    """Bucket course groups into the promised distance bands.

    Each course lands in exactly one band — the tightest one that contains it.
    That keeps the display non-redundant: a course 3 miles out appears under
    "within 5", not under all six headings.
    """
    buckets: dict[str, list[CourseGroup]] = {f"{b}mi": [] for b in BANDS_MI}

    for g in groups:
        for b in BANDS_MI:
            if g.distance_mi <= b:
                buckets[f"{b}mi"].append(g)
                break

    for key in buckets:
        buckets[key].sort(key=lambda g: (g.distance_mi, g.listings[0].tee_off))

    # Drop empty bands so callers don't render six empty headings.
    return {k: v for k, v in buckets.items() if v}
