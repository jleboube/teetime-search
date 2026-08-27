"""Normalized domain models shared across all providers."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class AuthModel(str, Enum):
    """How a provider grants access. Drives credential handling."""
    ANONYMOUS = "anonymous"          # public search, no auth
    PARTNER_API = "partner_api"      # app-level OAuth, our credentials
    USER_ACCOUNT = "user_account"    # tier 2: the user's own login


class PriceConfidence(str, Enum):
    HIGH = "high"      # provider states per-player, cart inclusion known
    MEDIUM = "medium"  # per-player known, cart inclusion inferred
    LOW = "low"        # normalization uncertain, show with a caveat


class Course(BaseModel):
    provider_course_id: str
    name: str
    lat: float
    lng: float
    city: Optional[str] = None
    state: Optional[str] = None
    # Populated by the dedupe stage; stable across providers.
    canonical_id: Optional[str] = None


class TeeTime(BaseModel):
    course: Course
    tee_off: datetime
    price_per_player: Optional[float] = None
    currency: str = "USD"
    price_confidence: PriceConfidence = PriceConfidence.MEDIUM
    includes_cart: Optional[bool] = None
    holes: int = 18
    slots_available: int
    provider: str
    booking_url: str
    # Straight-line miles from the search origin. Set by the banding stage.
    distance_mi: Optional[float] = None
    # True when this came from a tier-2 member account rather than public inventory.
    member_rate: bool = False


class ProviderStatus(BaseModel):
    """Per-provider outcome. Surfaced so partial results are never mistaken
    for complete ones."""
    provider: str
    ok: bool
    result_count: int = 0
    error: Optional[str] = None
    elapsed_ms: Optional[int] = None


class CourseGroup(BaseModel):
    """One physical course, with every listing found for it."""
    canonical_id: str
    name: str
    lat: float
    lng: float
    distance_mi: float
    listings: list[TeeTime]


class SearchRequest(BaseModel):
    lat: float
    lng: float
    date: str
    players: int = Field(default=4, ge=1, le=4)
    window_start: Optional[str] = None   # "07:00"
    window_end: Optional[str] = None     # "11:00"
    max_radius_mi: float = 35.0
    holes: Optional[int] = None


class SearchResponse(BaseModel):
    origin_lat: float
    origin_lng: float
    bands: dict[str, list[CourseGroup]]
    providers: list[ProviderStatus]
    generated_at: datetime

    @property
    def complete(self) -> bool:
        return all(p.ok for p in self.providers)
