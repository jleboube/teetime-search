"""Demo provider: deterministic synthetic inventory.

Exists so the whole pipeline — fan-out, banding, dedupe, caching, rendering —
can be exercised and demonstrated without partner credentials, which take
weeks to obtain. Everything it returns is fictional and clearly labeled
`provider: "demo"` with example.com booking URLs.

Determinism matters here: the same origin and date always produce the same
courses and tee times, so a demo search is reproducible and cacheable, and a
test can assert against it. Seeding from the rounded origin keeps nearby
searches (same town, different ZIP) looking like the same golf landscape.

Disabled unless the request explicitly opts in with
`provider_configs: {"demo": {"enabled": true}}` — synthetic data must never
mix silently into a real search.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

from ..models import Course, PriceConfidence, SearchRequest, TeeTime
from .base import TeeTimeProvider

# Fictional names chosen to exercise the renderer: long names that truncate,
# a multi-course facility ("North"/"South") that the dedupe layout-qualifier
# gate must keep separate, and a 9-hole course.
COURSE_NAMES = [
    "Willow Creek Golf Club",
    "Stonebridge Links",
    "Eagle Crest North",
    "Eagle Crest South",
    "Riverbend Municipal Golf Course",
    "Heather Glen Country Club",
    "Old Orchard Golf Course",
    "Prairie Winds Golf Links",
    "Copper Hollow Club",
    "Lakeside Par 3",
    "Timber Ridge Golf & Recreation Center",
    "Fox Run Golf Course",
]


class DemoProvider(TeeTimeProvider):
    name = "demo"
    timeout_s = 3.0

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled"))

    async def search(self, req: SearchRequest) -> list[TeeTime]:
        # One-degree rounding ≈ the same metro area seeds the same courses.
        seed = f"{round(req.lat)}:{round(req.lng)}:{req.date}"
        rng = random.Random(seed)

        day = datetime.fromisoformat(req.date)
        window_start = req.window_start or "06:30"
        window_end = req.window_end or "18:00"

        results: list[TeeTime] = []
        for idx, name in enumerate(COURSE_NAMES):
            # Fixed bearing per course, distance spread across all six bands.
            bearing = math.radians((idx * 137.5) % 360)  # golden angle: no clustering
            distance_mi = 2.0 + (idx / (len(COURSE_NAMES) - 1)) * 32.0
            if distance_mi > req.max_radius_mi:
                continue
            lat = req.lat + (distance_mi * math.cos(bearing)) / 69.0
            lng = req.lng + (distance_mi * math.sin(bearing)) / (
                69.0 * math.cos(math.radians(req.lat))
            )

            course = Course(
                provider_course_id=f"demo-{idx}",
                name=name,
                lat=round(lat, 5),
                lng=round(lng, 5),
            )

            nine_holes = "Par 3" in name
            base_price = rng.choice([22, 29, 34, 38, 45, 52, 65])
            tee = day.replace(hour=6, minute=30)
            close = day.replace(hour=17, minute=0)
            while tee <= close:
                tee += timedelta(minutes=rng.choice([8, 9, 10, 11, 12]) * 4)
                slots = rng.choice([1, 2, 2, 3, 4, 4, 4])
                if slots < req.players:
                    continue
                hhmm = tee.strftime("%H:%M")
                if not (window_start <= hhmm <= window_end):
                    continue
                if req.holes and req.holes != (9 if nine_holes else 18):
                    continue
                # Twilight pricing after 2pm, morning premium before 9 — makes
                # the rendered table look like real inventory economics.
                price = base_price * (0.7 if tee.hour >= 14 else 1.15 if tee.hour < 9 else 1.0)
                results.append(
                    TeeTime(
                        course=course,
                        tee_off=tee,
                        price_per_player=round(price, 0),
                        price_confidence=(
                            PriceConfidence.LOW if idx == 4 else PriceConfidence.HIGH
                        ),
                        includes_cart=not nine_holes,
                        holes=9 if nine_holes else 18,
                        slots_available=slots,
                        provider=self.name,
                        booking_url=f"https://example.com/demo/{course.provider_course_id}",
                    )
                )
        return results
