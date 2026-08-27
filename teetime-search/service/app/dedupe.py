"""Course identity resolution.

The same physical golf course routinely appears under several providers — on a
marketplace, on its own booking engine, and sometimes on a municipal portal —
with different names ("Cambridge Golf Course" vs "Cambridge GC" vs "Cambridge
Golf Course - Evansville") and different prices.

Collapsing these matters for two reasons: an undeduplicated list looks like
three courses when it's one, and the price comparison is the whole point.

Strategy: geo proximity gates the match, name similarity confirms it. Two
listings under 200m apart with similar names are the same course. Geo alone
would merge adjacent 9-hole layouts at a 36-hole facility; name alone would
merge every "Pine Valley" in the country.
"""
from __future__ import annotations

import hashlib
import re

from rapidfuzz import fuzz

from .geo import haversine_mi
from .models import CourseGroup, TeeTime

# Courses closer than this are candidates for merging.
PROXIMITY_M = 200
PROXIMITY_MI = PROXIMITY_M / 1609.344

# Token-set ratio above this confirms a name match.
NAME_THRESHOLD = 82

# Words that carry no distinguishing signal and inflate similarity scores.
NOISE = {
    "golf", "course", "club", "country", "cc", "gc", "links", "the", "at",
    "and", "of", "resort", "national", "municipal", "muni", "public",
}

# Multi-course facilities share a name and a parking lot: "Eagle Crest North"
# and "Eagle Crest South" are two distinct 18s at one address. Geo proximity
# can't separate them and the names are ~90% similar, so they need an explicit
# gate — merging them would hide half the facility's inventory.
LAYOUT_QUALIFIERS = {
    "north", "south", "east", "west",
    "red", "white", "blue", "gold", "green", "black", "silver",
    "championship", "tournament", "legacy", "heritage",
    "one", "two", "three", "i", "ii", "iii", "1", "2", "3",
}


def _qualifiers(normalized: str) -> set[str]:
    return set(normalized.split()) & LAYOUT_QUALIFIERS


def normalize_name(name: str) -> str:
    """Strip punctuation, casing, and boilerplate so 'Cambridge G.C.' and
    'Cambridge Golf Club' compare as the same string."""
    name = name.lower()
    # Drop a trailing city qualifier before stripping punctuation, or the
    # hyphen becomes a space and the split can never match:
    # "Helfrich Hills Golf Course - Evansville" -> "helfrich hills golf course"
    name = re.split(r"\s+[-\u2013]\s+", name)[0]
    name = re.sub(r"[^\w\s]", " ", name)
    tokens = [t for t in name.split() if t not in NOISE]
    # Abbreviations like "G.C." shatter into single letters that carry no
    # signal and drag the similarity score around. Drop them, but only if
    # something substantive survives.
    substantive = [t for t in tokens if len(t) > 1]
    tokens = substantive or tokens
    return " ".join(tokens) if tokens else name.strip()


def _canonical_id(name: str, lat: float, lng: float) -> str:
    """Stable identifier for a merged course. Rounded coordinates keep it
    consistent across providers that report slightly different centroids."""
    seed = f"{normalize_name(name)}|{lat:.3f}|{lng:.3f}"
    return hashlib.sha1(seed.encode()).hexdigest()[:12]


def _same_course(a: TeeTime, b: TeeTime) -> bool:
    d = haversine_mi(a.course.lat, a.course.lng, b.course.lat, b.course.lng)
    if d > PROXIMITY_MI:
        return False
    na, nb = normalize_name(a.course.name), normalize_name(b.course.name)

    # Distinct layouts at one facility: both name a layout and they disagree.
    qa, qb = _qualifiers(na), _qualifiers(nb)
    if qa and qb and not (qa & qb):
        return False

    return fuzz.token_set_ratio(na, nb) >= NAME_THRESHOLD


def group_courses(tee_times: list[TeeTime]) -> list[CourseGroup]:
    """Merge listings into one group per physical course.

    Nothing is dropped. Every listing survives into exactly one group, so a
    user comparing prices sees all of them.
    """
    if not tee_times:
        return []

    # Straightforward agglomerative pass: each listing joins the first cluster
    # it matches, else starts its own. Result sets are hundreds of courses at
    # most, so the quadratic worst case never bites in practice.
    clusters: list[list[TeeTime]] = []
    for tt in tee_times:
        for cluster in clusters:
            if _same_course(tt, cluster[0]):
                cluster.append(tt)
                break
        else:
            clusters.append([tt])

    groups: list[CourseGroup] = []
    for cluster in clusters:
        # Prefer the longest name as the display name — it's usually the most
        # complete ("Helfrich Hills Golf Course" over "Helfrich Hills").
        display = max((t.course.name for t in cluster), key=len)
        lat = sum(t.course.lat for t in cluster) / len(cluster)
        lng = sum(t.course.lng for t in cluster) / len(cluster)
        cid = _canonical_id(display, lat, lng)

        for t in cluster:
            t.course.canonical_id = cid

        cluster.sort(
            key=lambda t: (t.tee_off, t.price_per_player if t.price_per_player is not None else 1e9)
        )
        groups.append(
            CourseGroup(
                canonical_id=cid,
                name=display,
                lat=lat,
                lng=lng,
                distance_mi=min(
                    (t.distance_mi for t in cluster if t.distance_mi is not None),
                    default=0.0,
                ),
                listings=cluster,
            )
        )

    return groups
