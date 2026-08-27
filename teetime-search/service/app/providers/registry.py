"""Provider registration and construction.

Adapters are constructed per-request rather than held as singletons, because
tier-2 credentials arrive per-request from the caller's keychain and must not
outlive it.
"""
from __future__ import annotations

import httpx

from .base import TeeTimeProvider
from .chronogolf import ChronogolfProvider
from .demo import DemoProvider
from .golfnow import GolfNowProvider

REGISTRY: dict[str, type[TeeTimeProvider]] = {
    GolfNowProvider.name: GolfNowProvider,
    ChronogolfProvider.name: ChronogolfProvider,
    DemoProvider.name: DemoProvider,
}


def build_providers(
    client: httpx.AsyncClient, configs: dict[str, dict]
) -> list[TeeTimeProvider]:
    """Instantiate every adapter that has the access it needs.

    Adapters without credentials are omitted entirely rather than included and
    failing — a missing provider is a coverage gap, not an error, and the
    caller shouldn't see six identical 'not configured' failures on every search.
    """
    providers = []
    for name, cls in REGISTRY.items():
        provider = cls(client, configs.get(name, {}))
        if provider.enabled:
            providers.append(provider)
    return providers
