"""Search orchestration: fan out, band, dedupe."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from .dedupe import group_courses
from .geo import annotate_distance, band
from .models import ProviderStatus, SearchRequest, SearchResponse, TeeTime
from .providers.registry import build_providers


async def execute(req: SearchRequest, configs: dict[str, dict]) -> SearchResponse:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        providers = build_providers(client, configs)

        if not providers:
            return SearchResponse(
                origin_lat=req.lat,
                origin_lng=req.lng,
                bands={},
                providers=[
                    ProviderStatus(
                        provider="(none)",
                        ok=False,
                        error="no providers configured — see references/providers.md",
                    )
                ],
                generated_at=datetime.now(timezone.utc),
            )

        # Fan out concurrently. Each adapter isolates its own failures, so
        # gather never raises and one dead provider can't sink the search.
        outcomes = await asyncio.gather(*(p.run(req) for p in providers))

    tee_times: list[TeeTime] = []
    statuses: list[ProviderStatus] = []
    for results, status in outcomes:
        tee_times.extend(results)
        statuses.append(status)

    annotate_distance(tee_times, req.lat, req.lng)
    # Trim anything outside the outer band — providers interpret radius loosely.
    tee_times = [
        t for t in tee_times
        if t.distance_mi is not None and t.distance_mi <= req.max_radius_mi
    ]

    groups = group_courses(tee_times)

    return SearchResponse(
        origin_lat=req.lat,
        origin_lng=req.lng,
        bands=band(groups),
        providers=statuses,
        generated_at=datetime.now(timezone.utc),
    )
