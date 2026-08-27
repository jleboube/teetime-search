"""Provider adapter interface.

Everything platform-specific lives behind this interface: auth model, endpoint
shape, rate limits, response parsing. The pipeline above it never learns which
provider it's talking to.

Adding a platform means writing one subclass and registering it. In practice
the code is the easy part — obtaining access is where the real work is. See
references/providers.md.
"""
from __future__ import annotations

import abc
import asyncio
import time
from typing import Optional

import httpx

from ..models import AuthModel, ProviderStatus, SearchRequest, TeeTime


class ProviderError(Exception):
    """Adapter failed in a way that should degrade coverage, not the request."""


class TeeTimeProvider(abc.ABC):
    #: Stable slug used in results, config, and the credential keychain.
    name: str

    #: Determines whether this adapter needs user credentials.
    auth_model: AuthModel = AuthModel.ANONYMOUS

    #: Per-request ceiling. A slow provider must not hold up the whole search.
    timeout_s: float = 6.0

    #: Max concurrent in-flight requests to this provider.
    max_concurrency: int = 4

    def __init__(self, client: httpx.AsyncClient, config: Optional[dict] = None):
        self.client = client
        self.config = config or {}
        self._sem = asyncio.Semaphore(self.max_concurrency)

    @property
    def enabled(self) -> bool:
        """Adapters without the access they need stay out of the fan-out
        rather than failing every search."""
        return True

    @abc.abstractmethod
    async def search(self, req: SearchRequest) -> list[TeeTime]:
        """Return every tee time this provider knows about within
        req.max_radius_mi. Banding happens upstream — return the full radius.
        """
        raise NotImplementedError

    async def run(self, req: SearchRequest) -> tuple[list[TeeTime], ProviderStatus]:
        """Execute a search with timeout and error isolation.

        Never raises. A provider that fails returns empty results plus a status
        explaining why, so the caller can tell the user their results are
        incomplete instead of silently showing them less than exists.
        """
        started = time.monotonic()
        try:
            async with self._sem:
                results = await asyncio.wait_for(
                    self.search(req), timeout=self.timeout_s
                )
            elapsed = int((time.monotonic() - started) * 1000)
            return results, ProviderStatus(
                provider=self.name,
                ok=True,
                result_count=len(results),
                elapsed_ms=elapsed,
            )
        except asyncio.TimeoutError:
            return [], ProviderStatus(
                provider=self.name,
                ok=False,
                error=f"timed out after {self.timeout_s}s",
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception as exc:  # noqa: BLE001 - isolation is the point
            return [], ProviderStatus(
                provider=self.name,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )


class CredentialedProvider(TeeTimeProvider):
    """Tier-2 base: providers reachable only with the user's own login.

    Credentials arrive as environment variables injected at request time by the
    local skill wrapper, which reads them from the OS keychain. They are never
    baked into the image, written to the compose file, or persisted by the
    service.
    """

    auth_model = AuthModel.USER_ACCOUNT

    def __init__(self, client: httpx.AsyncClient, config: Optional[dict] = None):
        super().__init__(client, config)
        self._session_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("username") and self.config.get("password"))

    @abc.abstractmethod
    async def authenticate(self) -> str:
        """Exchange credentials for a session token. Called lazily and cached
        until expiry — re-authenticating on every search is both slow and the
        fastest way to trip a platform's bot detection."""
        raise NotImplementedError

    async def token(self) -> str:
        if self._session_token and time.time() < self._token_expires_at:
            return self._session_token
        self._session_token = await self.authenticate()
        # Conservative default; override in subclasses that report real expiry.
        self._token_expires_at = time.time() + 900
        return self._session_token
