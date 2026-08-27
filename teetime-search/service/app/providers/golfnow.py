"""GolfNow Affiliate & Partner API adapter.

Covers GolfNow, TeeOff, and EZLinks — all NBC Sports Next properties served by
the same partner API. This is the single largest source of public US inventory
and should be the first adapter you get working.

Access: apply at https://www.golfnow.com/business-partnership. The API is
REST/JSON over OAuth 2.0 with a sandbox environment, but credentials are
granted only after they review what you're building.

IMPORTANT: the endpoint paths and response field names below are placeholders
modelled on the API's documented shape. Verify every one against the sandbox
before trusting this adapter — do not assume these are correct.
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Optional

from ..models import (
    AuthModel,
    Course,
    PriceConfidence,
    SearchRequest,
    TeeTime,
)
from .base import ProviderError, TeeTimeProvider

API_ROOT = os.getenv("GOLFNOW_API_ROOT", "https://affiliate.gnsvc.com")
TOKEN_PATH = "/oauth2/token"          # VERIFY against sandbox
SEARCH_PATH = "/v1/teetimes/search"   # VERIFY against sandbox


class GolfNowProvider(TeeTimeProvider):
    name = "golfnow"
    auth_model = AuthModel.PARTNER_API
    timeout_s = 8.0
    max_concurrency = 6

    def __init__(self, client, config=None):
        super().__init__(client, config)
        # Partner credentials are app-level, not per-user, so they arrive via
        # the container environment (compose reads them from .env) rather than
        # the request body. Request config still wins if present.
        for field, env in (
            ("client_id", "GOLFNOW_CLIENT_ID"),
            ("client_secret", "GOLFNOW_CLIENT_SECRET"),
        ):
            if not self.config.get(field) and os.getenv(env):
                self.config[field] = os.getenv(env)
        self._token: Optional[str] = None
        self._expires_at: float = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("client_id") and self.config.get("client_secret"))

    async def _access_token(self) -> str:
        """Client-credentials grant. This is an app-level token belonging to
        the partner account, not to any user — no user credentials involved."""
        if self._token and time.time() < self._expires_at:
            return self._token

        resp = await self.client.post(
            f"{API_ROOT}{TOKEN_PATH}",
            data={
                "grant_type": "client_credentials",
                "client_id": self.config["client_id"],
                "client_secret": self.config["client_secret"],
            },
        )
        if resp.status_code != 200:
            raise ProviderError(f"token request failed: {resp.status_code}")

        payload = resp.json()
        self._token = payload["access_token"]
        # Refresh a minute early to avoid racing expiry mid-search.
        self._expires_at = time.time() + payload.get("expires_in", 3600) - 60
        return self._token

    async def search(self, req: SearchRequest) -> list[TeeTime]:
        token = await self._access_token()

        params = {
            "latitude": req.lat,
            "longitude": req.lng,
            "radius": int(req.max_radius_mi),
            "date": req.date,
            "players": req.players,
        }
        if req.window_start:
            params["earliestTeeTime"] = req.window_start
        if req.window_end:
            params["latestTeeTime"] = req.window_end
        if req.holes:
            params["holes"] = req.holes

        resp = await self.client.get(
            f"{API_ROOT}{SEARCH_PATH}",
            params=params,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        if resp.status_code == 429:
            raise ProviderError("rate limited")
        if resp.status_code != 200:
            raise ProviderError(f"search failed: {resp.status_code}")

        return [self._parse(item) for item in resp.json().get("teeTimes", [])]

    def _parse(self, item: dict) -> TeeTime:
        facility = item.get("facility", {})
        course = Course(
            provider_course_id=str(facility.get("id", "")),
            name=facility.get("name", "Unknown course"),
            lat=float(facility.get("latitude", 0.0)),
            lng=float(facility.get("longitude", 0.0)),
            city=facility.get("city"),
            state=facility.get("state"),
        )

        # GolfNow rates are generally per-player. Cart inclusion varies by
        # rate type, so confidence drops when we can't determine it.
        includes_cart = item.get("cartIncluded")
        confidence = (
            PriceConfidence.HIGH if includes_cart is not None else PriceConfidence.MEDIUM
        )

        return TeeTime(
            course=course,
            tee_off=datetime.fromisoformat(item["teeOffTime"]),
            price_per_player=item.get("displayRate"),
            price_confidence=confidence,
            includes_cart=includes_cart,
            holes=item.get("holes", 18),
            slots_available=item.get("playersAvailable", 4),
            provider=self.name,
            booking_url=item.get("bookingUrl", ""),
        )
