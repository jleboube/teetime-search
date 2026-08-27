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
  requirements.txt           host-side deps (httpx, keyring)
  scripts/search.py          CLI the skill invokes; runs on host
  scripts/creds.py           keychain broker; runs on host
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
    app/providers/golfnow.py partner API (UNVERIFIED endpoints)
    app/providers/chronogolf.py Lightspeed partner API v2
    app/providers/demo.py    deterministic synthetic inventory, opt-in only
    tests/test_dedupe.py     dedupe regression tests
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

**Demo provider:** `app/providers/demo.py` generates deterministic fictional
inventory. It is opt-in only (`--demo` on the CLI → `provider_configs.demo.
enabled`); synthetic data must never mix silently into a real search.

**Written but unverified:** the GolfNow and Chronogolf adapters. The GolfNow
endpoint paths and field names are modelled on the documented API shape but
have *not* been checked against the sandbox. Do not trust a result from either
adapter until they are validated against real credentials.

## Phased plan

### Phase 1 — Make one provider real
Apply for GolfNow affiliate access. When credentials arrive, validate
`golfnow.py` against the sandbox and correct every endpoint path and field
name. Write `tests/test_golfnow.py` with recorded fixtures. Until this is done
the product does not work.

### Phase 2 — Prove the pipeline end to end
Build the ZIP database, bring up the stack, and run real searches across five
metro areas. Manually verify dedupe against courses you can check by hand.
Confirm p95 latency under 6 seconds.

### Phase 3 — Tier-2 for real accounts
Determine which platforms the user actually holds accounts on, then implement
those adapters. Chronogolf is scaffolded; others need writing. Each one needs
its consent copy reviewed before shipping.

### Phase 4 — Usability
Time-window and price filters, 9-hole handling, saved searches. Consider
alerting — desirable tee times get taken within minutes of release, which is
the single strongest feature idea in the product and also the one most likely
to draw platform attention. Weigh that before building it.

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
