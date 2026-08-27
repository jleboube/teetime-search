"""Enabled-gating regression tests.

The invariant: an adapter without the access it needs must drop out of the
fan-out entirely, and the demo provider must never appear without explicit
opt-in. A regression here either fails every search with credential errors or
silently mixes fictional inventory into real results.
"""
from __future__ import annotations

import httpx
import pytest

from app.providers.registry import REGISTRY, build_providers


@pytest.fixture
def client():
    return httpx.AsyncClient()


def test_nothing_enabled_without_config(client):
    assert build_providers(client, {}) == []


def test_demo_requires_explicit_opt_in(client):
    providers = build_providers(client, {"demo": {"enabled": True}})
    assert [p.name for p in providers] == ["demo"]
    # Truthiness alone isn't enough — an empty config must not enable it.
    assert build_providers(client, {"demo": {}}) == []


def test_partial_credentials_do_not_enable(client):
    partials = {
        "golfnow": {"client_id": "x"},                      # missing secret
        "chronogolf": {"access_token": "x"},                # missing club_ids
        "foreup": {"username": "x", "password": "y"},       # missing course info
        "teesnap": {"subdomain": "club", "username": "x"},  # missing password/zip
    }
    for name, config in partials.items():
        assert build_providers(client, {name: config}) == [], name


def test_full_credentials_enable_each_tier2(client):
    full = {
        "chronogolf": {"access_token": "t", "club_ids": ["uuid"]},
        "foreup": {
            "username": "u",
            "password": "p",
            "course_id": "19348",
            "schedule_ids": ["2431"],
            "course_zips": ["47714"],
        },
        "teesnap": {
            "subdomain": "club",
            "username": "u",
            "password": "p",
            "course_zip": "47714",
        },
    }
    for name, config in full.items():
        providers = build_providers(client, {name: config})
        assert [p.name for p in providers] == [name]


def test_every_registered_provider_has_a_name():
    for key, cls in REGISTRY.items():
        assert key == cls.name
