"""Teesnap user-account adapter.

Teesnap has no public or partner API. Each club runs its own instance at
{subdomain}.teesnap.net, and a member login can read that club's tee sheet the
way the club's own booking site does. Like foreup.py, this is a connection to
the user's own club under their own account, running locally — never a
discovery source. The ToS warning in creds.py applies with full force.

IMPORTANT: unlike foreUP, Teesnap's browser API has not been mapped even
informally. Every endpoint path and field name below is a PLACEHOLDER for the
shape such an API usually takes. This adapter WILL NOT work until someone with
a Teesnap club login walks the site with browser dev tools open and corrects
it. Steps: log in at the club's subdomain, open the network tab, load the tee
sheet for a date, and copy the real login and availability requests into
authenticate() and search().

Config (collected by creds.py):
    subdomain             the club's {subdomain}.teesnap.net
    username, password    the user's Teesnap login
    course_zip            the club's ZIP (no coordinates in any known response)
"""
from __future__ import annotations

from datetime import datetime

from ..geo import UnknownZip, haversine_mi, zip_to_latlng
from ..models import Course, PriceConfidence, SearchRequest, TeeTime
from .base import CredentialedProvider, ProviderError

LOGIN_PATH = "/api/authenticate"      # PLACEHOLDER — capture the real one
TIMES_PATH = "/api/tee-times"         # PLACEHOLDER — capture the real one


class TeesnapProvider(CredentialedProvider):
    name = "teesnap"
    timeout_s = 8.0
    max_concurrency = 2

    @property
    def enabled(self) -> bool:
        return bool(
            self.config.get("subdomain")
            and self.config.get("username")
            and self.config.get("password")
            and self.config.get("course_zip")
        )

    @property
    def _root(self) -> str:
        return f"https://{self.config['subdomain']}.teesnap.net"

    async def authenticate(self) -> str:
        resp = await self.client.post(
            f"{self._root}{LOGIN_PATH}",
            json={
                "email": self.config["username"],
                "password": self.config["password"],
            },
        )
        if resp.status_code != 200:
            raise ProviderError(
                f"login failed ({resp.status_code}) — the Teesnap adapter is a "
                "placeholder; see providers/teesnap.py for how to complete it"
            )
        token = resp.json().get("token")
        if not token:
            raise ProviderError("no token in login response — endpoint shape differs")
        return token

    async def search(self, req: SearchRequest) -> list[TeeTime]:
        try:
            lat, lng = zip_to_latlng(self.config["course_zip"])
        except UnknownZip as exc:
            raise ProviderError(str(exc)) from exc

        if haversine_mi(req.lat, req.lng, lat, lng) > req.max_radius_mi:
            return []

        token = await self.token()
        resp = await self.client.get(
            f"{self._root}{TIMES_PATH}",
            params={"date": req.date, "players": req.players},
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200:
            raise ProviderError(f"tee sheet request failed: {resp.status_code}")

        results: list[TeeTime] = []
        for row in resp.json().get("teeTimes", []):
            try:
                tee_off = datetime.fromisoformat(row["startTime"])
            except (KeyError, ValueError):
                continue
            hhmm = tee_off.strftime("%H:%M")
            if req.window_start and hhmm < req.window_start:
                continue
            if req.window_end and hhmm > req.window_end:
                continue
            slots = int(row.get("openSlots", 0))
            if slots < req.players:
                continue

            results.append(
                TeeTime(
                    course=Course(
                        provider_course_id=self.config["subdomain"],
                        name=row.get("courseName", self.config["subdomain"]),
                        lat=lat,
                        lng=lng,
                    ),
                    tee_off=tee_off,
                    price_per_player=row.get("price"),
                    price_confidence=PriceConfidence.LOW,
                    holes=int(row.get("holes", 18)),
                    slots_available=slots,
                    provider=self.name,
                    booking_url=self._root,
                    member_rate=True,
                )
            )
        return results
