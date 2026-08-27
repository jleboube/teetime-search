"""foreUP user-account adapter.

foreUP has no partner API available to independent developers. What it does
have is the JSON API its own customer-facing booking pages are built on, and a
user with a member login can read their club's tee sheet through it exactly as
their browser does. This adapter acts as that user, against their own account,
running locally — it is a connection to the user's club, not a discovery
source.

Tell the user before storing credentials (creds.py does): automated access may
violate foreUP's terms of service and the account risk is theirs.

IMPORTANT: the endpoint paths and field names below are modelled on the API
observed behind foreUP's public booking pages, which is undocumented and can
change without notice. Verify against the user's actual club on first use —
run one search and compare it with the club's booking page before trusting it.

Config (collected by creds.py):
    username, password    the user's foreUP login
    course_id             from the club's booking URL:
                          foreupsoftware.com/index.php/booking/{course_id}/{schedule_id}
    schedule_ids          one per course/layout at the facility
    course_zips           ZIP per schedule (the API returns no coordinates,
                          and a listing without coordinates can't be banded)
    booking_class_id      optional; some clubs gate member rates behind one
"""
from __future__ import annotations

import os
from datetime import datetime

from ..geo import UnknownZip, haversine_mi, zip_to_latlng
from ..models import Course, PriceConfidence, SearchRequest, TeeTime
from .base import CredentialedProvider, ProviderError

API_ROOT = os.getenv("FOREUP_API_ROOT", "https://foreupsoftware.com")
LOGIN_PATH = "/index.php/api/booking/users/login"   # VERIFY on first use
TIMES_PATH = "/index.php/api/booking/times"          # VERIFY on first use


class ForeUpProvider(CredentialedProvider):
    name = "foreup"
    timeout_s = 8.0
    max_concurrency = 2  # be gentle: this is a member account, not a partner key

    @property
    def enabled(self) -> bool:
        return bool(
            self.config.get("username")
            and self.config.get("password")
            and self.config.get("course_id")
            and self.config.get("schedule_ids")
            and self.config.get("course_zips")
        )

    async def authenticate(self) -> str:
        data = {
            "username": self.config["username"],
            "password": self.config["password"],
            "course_id": self.config["course_id"],
            # The public booking pages send this literal value.
            "api_key": "no_limits",
        }
        if self.config.get("booking_class_id"):
            data["booking_class_id"] = self.config["booking_class_id"]

        resp = await self.client.post(f"{API_ROOT}{LOGIN_PATH}", data=data)
        if resp.status_code != 200:
            raise ProviderError(f"login failed: {resp.status_code}")
        body = resp.json()
        jwt = body.get("jwt")
        if not jwt:
            raise ProviderError("login succeeded but no jwt in response — check credentials")
        return jwt

    async def search(self, req: SearchRequest) -> list[TeeTime]:
        token = await self.token()

        schedule_ids = self.config["schedule_ids"]
        zips = self.config["course_zips"]
        # A single ZIP covers every schedule at a one-location facility.
        if len(zips) == 1:
            zips = zips * len(schedule_ids)
        if len(zips) != len(schedule_ids):
            raise ProviderError(
                f"{len(schedule_ids)} schedule_ids but {len(zips)} course_zips"
            )

        results: list[TeeTime] = []
        resolved_any = False
        for schedule_id, zip_code in zip(schedule_ids, zips):
            try:
                lat, lng = zip_to_latlng(zip_code)
            except UnknownZip:
                # Can't band what we can't place; skip this course rather than
                # inventing a location.
                continue
            resolved_any = True

            if haversine_mi(req.lat, req.lng, lat, lng) > req.max_radius_mi:
                continue

            day = datetime.fromisoformat(req.date)
            params = {
                "time": "all",
                "date": day.strftime("%m-%d-%Y"),
                "holes": "all",
                "players": req.players,
                "schedule_id": schedule_id,
                "specials_only": 0,
                "api_key": "no_limits",
            }
            if self.config.get("booking_class_id"):
                params["booking_class"] = self.config["booking_class_id"]

            resp = await self.client.get(
                f"{API_ROOT}{TIMES_PATH}",
                params=params,
                headers={
                    "api-key": "no_limits",
                    "x-authorization": f"Bearer {token}",
                },
            )
            if resp.status_code == 401:
                raise ProviderError("session rejected — credentials may have changed")
            if resp.status_code != 200:
                raise ProviderError(f"schedule {schedule_id}: {resp.status_code}")

            for row in resp.json():
                tt = self._parse(row, schedule_id, lat, lng, req)
                if tt is not None:
                    results.append(tt)

        if not resolved_any:
            raise ProviderError("no course_zips resolved to a location")
        return results

    def _parse(
        self, row: dict, schedule_id: str, lat: float, lng: float, req: SearchRequest
    ) -> TeeTime | None:
        try:
            tee_off = datetime.strptime(row["time"], "%Y-%m-%d %H:%M")
        except (KeyError, ValueError):
            return None

        hhmm = tee_off.strftime("%H:%M")
        if req.window_start and hhmm < req.window_start:
            return None
        if req.window_end and hhmm > req.window_end:
            return None

        slots = int(row.get("available_spots", 0))
        if slots < req.players:
            return None

        holes = int(row.get("holes", 18))
        if req.holes and req.holes != holes:
            return None

        green = row.get("green_fee")
        cart = row.get("cart_fee")
        if green is not None and cart is not None:
            price = float(green) + float(cart)
            includes_cart = True
            confidence = PriceConfidence.MEDIUM  # member vs guest rate unverified
        elif green is not None:
            price = float(green)
            includes_cart = None
            confidence = PriceConfidence.LOW
        else:
            price, includes_cart, confidence = None, None, PriceConfidence.LOW

        return TeeTime(
            course=Course(
                provider_course_id=str(schedule_id),
                name=row.get("course_name")
                or row.get("schedule_name")
                or f"foreUP schedule {schedule_id}",
                lat=lat,
                lng=lng,
            ),
            tee_off=tee_off,
            price_per_player=price,
            price_confidence=confidence,
            includes_cart=includes_cart,
            holes=holes,
            slots_available=slots,
            provider=self.name,
            booking_url=(
                f"{API_ROOT}/index.php/booking/"
                f"{self.config['course_id']}/{schedule_id}#/teetimes"
            ),
            member_rate=True,
        )
