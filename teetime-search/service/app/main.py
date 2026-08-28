"""FastAPI entrypoint.

Tier-2 credentials arrive in the request body from the local skill wrapper,
which read them from the user's OS keychain. The service holds them for the
life of the request and never persists them. Bind to localhost only.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Optional

try:
    import redis.asyncio as aioredis
except ImportError:  # no-Docker mode installs without the redis client
    aioredis = None

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .geo import UnknownZip, zip_to_latlng
from .models import SearchRequest, SearchResponse
from .search import execute

app = FastAPI(title="Tee Time Aggregator", version="0.1.0")

# Unset REDIS_URL (the no-Docker mode) means the in-process cache below.
# The compose file sets it explicitly for the containerized stack.
REDIS_URL = os.getenv("REDIS_URL")
# Tee time inventory turns over fast. A cache older than this actively
# misleads — a golfer shown a slot that was taken two minutes ago will
# bounce off a sold-out booking page.
INVENTORY_TTL_S = 90


class MemoryCache:
    """Redis-shaped in-process TTL cache. The cache's only job here is 90
    seconds of inventory reuse in a single process — a second service and a
    Docker daemon are a lot of machinery for a dict with timestamps."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, str]] = {}

    async def get(self, key: str) -> Optional[str]:
        hit = self._store.get(key)
        if hit is None:
            return None
        expires, value = hit
        if time.monotonic() > expires:
            self._store.pop(key, None)
            return None
        return value

    async def setex(self, key: str, ttl: int, value: str) -> None:
        if len(self._store) > 512:  # long-running process hygiene
            now = time.monotonic()
            for k in [k for k, (exp, _) in self._store.items() if exp < now]:
                self._store.pop(k, None)
        self._store[key] = (time.monotonic() + ttl, value)

    async def aclose(self) -> None:
        self._store.clear()


_cache = None


@app.on_event("startup")
async def startup() -> None:
    global _cache
    if REDIS_URL and aioredis is not None:
        _cache = aioredis.from_url(REDIS_URL, decode_responses=True)
    else:
        _cache = MemoryCache()


@app.on_event("shutdown")
async def shutdown() -> None:
    if _cache:
        await _cache.aclose()


class SearchPayload(BaseModel):
    zip_code: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    date: str
    players: int = 4
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    max_radius_mi: float = 35.0
    holes: Optional[int] = None
    # Per-provider config, including tier-2 credentials. Request-scoped.
    provider_configs: dict[str, dict] = {}


def _cache_key(req: SearchRequest, provider_names: list[str]) -> str:
    """Credentials are deliberately excluded from the key; provider names are
    included so a credentialed search never serves an anonymous cached result."""
    seed = json.dumps(
        {
            "lat": round(req.lat, 3),
            "lng": round(req.lng, 3),
            "date": req.date,
            "players": req.players,
            "ws": req.window_start,
            "we": req.window_end,
            "r": req.max_radius_mi,
            "p": sorted(provider_names),
        },
        sort_keys=True,
    )
    return "tt:" + hashlib.sha1(seed.encode()).hexdigest()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/search", response_model=SearchResponse)
async def search(payload: SearchPayload) -> SearchResponse:
    if payload.lat is not None and payload.lng is not None:
        lat, lng = payload.lat, payload.lng
    elif payload.zip_code:
        try:
            lat, lng = zip_to_latlng(payload.zip_code)
        except UnknownZip as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        raise HTTPException(
            status_code=400, detail="provide either zip_code or lat/lng"
        )

    req = SearchRequest(
        lat=lat,
        lng=lng,
        date=payload.date,
        players=payload.players,
        window_start=payload.window_start,
        window_end=payload.window_end,
        max_radius_mi=payload.max_radius_mi,
        holes=payload.holes,
    )

    key = _cache_key(req, list(payload.provider_configs.keys()))
    if _cache:
        cached = await _cache.get(key)
        if cached:
            return SearchResponse.model_validate_json(cached)

    resp = await execute(req, payload.provider_configs)

    # Only cache complete results. Caching a partial response would pin a
    # transient provider outage in place for its full TTL.
    if _cache and resp.complete:
        await _cache.setex(key, INVENTORY_TTL_S, resp.model_dump_json())

    return resp
