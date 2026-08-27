"""Lightspeed Golf (formerly Chronogolf) Partner API v2 adapter.

This is a tier-2 source in practice. The partner API is club-scoped: credentials
are provisioned by hand (email golf.api@lightspeedhq.com) and each club must be
individually added to your integration. There is no radius search across all
Lightspeed clubs — you query the clubs you have access to and filter by distance
locally.

That makes it the right adapter for a user's own club rather than a discovery
source. Configure the club UUIDs the user actually belongs to.

New integrations must use V2. V1 ids are integers and V2 ids are UUIDs; they are
not interchangeable, so a V1 id will 404 against a V2 endpoint.
"""
from __future__ import annotations

import os
from datetime import datetime

from ..geo import haversine_mi
from ..models import Course, PriceConfidence, SearchRequest, TeeTime
from .base import CredentialedProvider, ProviderError

API_ROOT = os.getenv("CHRONOGOLF_API_ROOT", "https://apis.chronogolf.com")
PARTNER_V2 = "/partner_api/v2"


class ChronogolfProvider(CredentialedProvider):
    name = "chronogolf"
    timeout_s = 7.0
    max_concurrency = 3

    @property
    def enabled(self) -> bool:
        # Needs both an OAuth token and at least one club to query.
        return bool(self.config.get("access_token") and self.config.get("club_ids"))

    async def authenticate(self) -> str:
        # Lightspeed provisions the token out of band; there is no login flow
        # to automate. If that changes, implement the exchange here.
        token = self.config.get("access_token")
        if not token:
            raise ProviderError("no access_token configured")
        return token

    async def search(self, req: SearchRequest) -> list[TeeTime]:
        token = await self.token()
        results: list[TeeTime] = []

        for club_id in self.config["club_ids"]:
            resp = await self.client.get(
                f"{API_ROOT}{PARTNER_V2}/clubs/{club_id}/tee_times",
                params={"date": req.date, "players": req.players},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.api+json",
                },
            )
            if resp.status_code == 404:
                # Club not attached to this integration — skip rather than
                # failing the whole provider.
                continue
            if resp.status_code != 200:
                raise ProviderError(f"club {club_id}: {resp.status_code}")

            body = resp.json()
            included = {
                (r["type"], r["id"]): r for r in body.get("included", [])
            }
            for row in body.get("data", []):
                tt = self._parse(row, included, club_id)
                if tt is None:
                    continue
                # Club-scoped API returns everything; enforce the radius here.
                d = haversine_mi(req.lat, req.lng, tt.course.lat, tt.course.lng)
                if d <= req.max_radius_mi:
                    results.append(tt)

        return results

    def _parse(self, row: dict, included: dict, club_id: str) -> TeeTime | None:
        attrs = row.get("attributes", {})
        club = included.get(("clubs", club_id), {}).get("attributes", {})

        lat, lng = club.get("latitude"), club.get("longitude")
        if lat is None or lng is None:
            # Without coordinates we can't band it; better to omit than to
            # place it at the origin and claim it's zero miles away.
            return None

        return TeeTime(
            course=Course(
                provider_course_id=club_id,
                name=club.get("name", "Unknown club"),
                lat=float(lat),
                lng=float(lng),
                city=club.get("city"),
                state=club.get("state"),
            ),
            tee_off=datetime.fromisoformat(attrs["start_time"]),
            price_per_player=attrs.get("green_fee"),
            price_confidence=PriceConfidence.LOW,  # member vs guest rates vary
            includes_cart=attrs.get("cart_included"),
            holes=attrs.get("holes", 18),
            slots_available=attrs.get("available_spots", 4),
            provider=self.name,
            booking_url=attrs.get("booking_url", ""),
            member_rate=True,
        )
