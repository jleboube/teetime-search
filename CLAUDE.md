# CLAUDE.md — Tee Time Aggregator

Handoff document for Claude Code. Read this before touching anything here.

## What this is

A local Claude Skill that searches golf tee times across booking platforms and
returns results banded by distance from a ZIP code or shared location. The
skill folder is self-contained (that's what gets zipped and shared) and has
two halves:

- `teetime-search/` (top level) — runs natively on the host. SKILL.md, the
  search CLI, the credential broker.
- `teetime-search/service/` — Dockerized FastAPI aggregator. Provider
  adapters, dedupe, banding, cache.

The split is not stylistic. Containers cannot reach the OS keychain, so the
credential broker must be native while the adapters stay containerized.

## Layout

```
PRD.md                       product requirements
CLAUDE.md                    this file
README.md                    public-facing docs
teetime-search/              the distributable skill folder
  SKILL.md                   skill definition + result formatting rules
  requirements.txt           host-side deps (httpx, keyring, rich)
  scripts/search.py          CLI the skill invokes; rich terminal tee sheet
  scripts/creds.py           keychain broker; runs on host
  scripts/prefs.py           play-pattern preferences (~/.config/teetime/)
  scripts/watch.py           launchd watcher; iMessage/notification digests
  references/providers.md    per-platform access model
  references/credentials.md  security posture
  service/
    docker-compose.yml       api + redis, loopback-bound
    Dockerfile               builds ZIP db at image build time
    scripts/build_zip_db.py  Census ZCTA gazetteer -> SQLite
    app/main.py              FastAPI entrypoint, caching
    app/search.py            fan-out orchestration
    app/geo.py               ZIP resolution, haversine, banding
    app/dedupe.py            course identity resolution
    app/models.py            normalized domain models
    app/providers/base.py    adapter interface
    app/providers/golfnow.py partner API, optional (UNVERIFIED endpoints)
    app/providers/chronogolf.py Lightspeed partner API v2
    app/providers/foreup.py  user-account adapter (verify on first connection)
    app/providers/teesnap.py user-account adapter (PLACEHOLDER endpoints)
    app/providers/demo.py    deterministic synthetic inventory, opt-in only
    tests/test_dedupe.py     dedupe regression tests
    tests/test_providers.py  enabled-gating regression tests
```

## Invariants

Violating any of these is a bug even if tests pass.

1. **No credential is ever written to disk, image, compose file, or log.** The
   OS keychain is the only store. See `references/credentials.md` for the
   verification commands.
2. **The API binds to 127.0.0.1 only.** It receives user credentials in request
   bodies. If a change exposes it on 0.0.0.0, that change is wrong.
3. **A failing provider degrades coverage, never the request.** Adapters raise
   `ProviderError`; `TeeTimeProvider.run()` converts it into a `ProviderStatus`.
   Never return `[]` on failure — that makes an outage look like no availability.
4. **Every response reports which providers answered.** Partial results
   presented as complete cause real harm: a golfer books the wrong thing.
5. **Dedupe never drops a listing.** Listings collapse into groups. The count in
   must equal the count out. `test_no_listing_is_ever_dropped` guards this.
6. **One radius query, banded client-side.** Never issue six requests for six
   bands. The 35-mile result is a strict superset.
7. **Only complete responses get cached.** Caching a partial result pins a
   transient outage in place for its full TTL.
8. **The skill never completes a booking.** Deep-link and hand off.

## Conventions

- Python 3.12, `from __future__ import annotations` everywhere.
- Pydantic v2 models are the contract between layers. Adapters return
  `list[TeeTime]` and nothing else.
- Async throughout the service. Adapters use the shared `httpx.AsyncClient`.
- Comments explain *why*, not *what*. The non-obvious decisions — the layout
  qualifier gate, the 90-second TTL, the native/container split — all carry
  their reasoning inline. Preserve that when editing.
- Adapters are constructed per-request, never cached as singletons, because
  tier-2 configs carry credentials that must not outlive the request.

## Current state

**Working and tested:** models, geo banding, dedupe (5 passing regression
tests), fan-out orchestration, FastAPI surface, credential broker, compose
stack. The whole pipeline has been verified end to end (2026-08-27) via the
demo provider: image builds, ZIP db writes 33,791 rows, `/health` answers,
`scripts/search.py --demo` returns banded results, cache and error paths
behave.

**Watcher (2026-08-27):** prefs.py + watch.py deliver the Phase-4 alerting
idea as a local launchd job: daily checks anchored to booking-window opening
(lead_days), snapshot diffing so it only messages on new inventory, delivery
via the user's own Messages.app (osascript) with macOS-notification fallback.
Verified end to end in demo mode. Deliberately capped at one search per
watched date per run — see the ToS note in Phase 4.

**Demo provider:** `app/providers/demo.py` generates deterministic fictional
inventory. It is opt-in only (`--demo` on the CLI → `provider_configs.demo.
enabled`); synthetic data must never mix silently into a real search.

**Written but unverified:** every live adapter, to different degrees.
`foreup.py` follows the API observed behind foreUP's own booking pages and is
the closest to working; verify against a real member login on first
connection. `chronogolf.py` follows the documented Partner API v2 shape.
`teesnap.py` is an explicit placeholder — its docstring says how to complete
it. `golfnow.py` (optional partner path) is modelled on the documented shape
but unchecked against the sandbox. Do not trust a result from any of them
until validated against real credentials.

## Product direction (2026-08-27)

The product is **connection-based**: users link the booking platforms they
hold accounts on, with their own credentials, and coverage is the union of
their connections. The GolfNow partner API is an optional operator add-on for
public discovery inventory, not a prerequisite. The skill's conversational
job on initiation is to collect date, total golfers, and origin (asking once,
together, for whatever is missing), then fan out across connections.

## Phased plan

### Phase 1 — Validate the connection adapters against real accounts
Connect a real foreUP login (most golfers with a home course have one) and
verify `foreup.py` end to end; correct endpoints and record fixtures into
`tests/test_foreup.py`. Complete `teesnap.py` when someone with a Teesnap
club login can capture the real endpoints. Chronogolf when partner access to
the user's club exists.

### Phase 2 — Prove the pipeline with live connections
Run real searches through connected accounts across several dates. Manually
verify dedupe against courses you can check by hand. Confirm p95 latency
under 6 seconds.

### Phase 3 — Broaden the connection catalog
Club Prophet, Golf18/TenFore, CourseRev, and a GolfNow consumer-account
adapter (their bot defenses make it the riskiest — weigh carefully). Each
new platform needs its consent copy reviewed before shipping. Optionally,
apply for GolfNow affiliate access to add public discovery inventory.

### Phase 4 — Usability
Time-window and price filters, 9-hole handling, saved searches. Alerting is
now built (the watcher) but deliberately gentle: one check per watched date
per day. Resist making it poll faster — release-sniping cadence is the
single likeliest way to draw platform attention and user account
suspensions.

## Gotchas

- `rapidfuzz.token_set_ratio` is order-insensitive, which is why "Novadell
  Links" matches "The Links at Novadell". It also merges multi-course
  facilities, which is what `LAYOUT_QUALIFIERS` exists to prevent. Extending
  that set is usually the right fix for a bad merge.
- `normalize_name` strips the city qualifier *before* stripping punctuation.
  Reversing that order silently breaks the split — the hyphen becomes a space
  and the regex stops matching. There is a test for this.
- ZCTAs are not identical to USPS ZIPs; a few PO-box-only ZIPs have no centroid
  and raise `UnknownZip`. That's correct behavior, not a bug to paper over.
- Distance is straight-line. Never present the bands as drive time.
