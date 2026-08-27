"""Dedupe regression tests.

Course identity resolution is the part of this system most likely to silently
degrade — a bad merge hides inventory, a bad split shows one course twice.
Both failure modes are invisible without these.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.dedupe import group_courses, normalize_name
from app.geo import annotate_distance
from app.models import Course, TeeTime


def tt(name, lat, lng, provider="golfnow", price=40.0):
    return TeeTime(
        course=Course(provider_course_id="x", name=name, lat=lat, lng=lng),
        tee_off=datetime(2026, 8, 29, 8, 0),
        price_per_player=price,
        slots_available=4,
        provider=provider,
        booking_url="https://example.com",
    )


def n_groups(listings):
    annotate_distance(listings, 38.0, -87.57)
    return len(group_courses(listings))


def test_name_variants_normalize_alike():
    assert normalize_name("Cambridge Golf Course") == normalize_name("Cambridge G.C.")
    assert normalize_name("Helfrich Hills Golf Course - Evansville") == normalize_name("Helfrich Hills")
    assert normalize_name("Rolling Hills Country Club") == normalize_name("Rolling Hills CC")


def test_same_name_different_states_stay_separate():
    # Geo gate: name alone would merge every "Pine Valley" in the country.
    assert n_groups([
        tt("Pine Valley Golf Club", 39.78, -74.97),
        tt("Pine Valley Golf Club", 38.00, -87.57),
    ]) == 2


def test_multi_course_facility_stays_separate():
    # Name gate: geo alone would merge two 18s sharing a clubhouse.
    assert n_groups([
        tt("Eagle Crest North Course", 38.000, -87.570),
        tt("Eagle Crest South Course", 38.001, -87.571),
    ]) == 2


def test_one_course_across_three_providers_merges():
    assert n_groups([
        tt("Cambridge Golf Course", 38.0400, -87.6100, "golfnow", 38),
        tt("Cambridge G.C.", 38.0401, -87.6101, "foreup", 34),
        tt("Cambridge Golf Course - Evansville", 38.0399, -87.6099, "teesnap", 36),
    ]) == 1


def test_no_listing_is_ever_dropped():
    listings = [
        tt("Cambridge Golf Course", 38.0400, -87.6100, "golfnow"),
        tt("Cambridge G.C.", 38.0401, -87.6101, "foreup"),
        tt("Fendrich Golf Course", 37.9900, -87.5400, "golfnow"),
    ]
    annotate_distance(listings, 38.0, -87.57)
    groups = group_courses(listings)
    assert sum(len(g.listings) for g in groups) == len(listings)
