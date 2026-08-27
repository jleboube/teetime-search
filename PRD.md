# PRD — Tee Time Aggregator Skill

**Owner:** Joe LeBoube / ArchBadger500, LLC
**Status:** Draft v0.1
**Date:** August 2026

---

## 1. Problem

Public golf inventory is fragmented across a dozen booking platforms. A golfer
looking for a Saturday morning slot within reasonable driving distance has to
check GolfNow, then their club's own portal, then a muni's system, then compare
prices manually. No single search covers both public marketplace inventory and
the member-only tee sheets the golfer actually has access to.

## 2. Goal

One query — location plus date plus constraints — returns a deduplicated,
distance-banded list of bookable tee times across every platform the user can
reach, including ones that require their own login.

## 3. Non-goals

- **Automated booking.** We surface and deep-link. We do not complete purchases.
- **Credential custody.** We never store or transmit user credentials to
  infrastructure we operate. See §7.
- **Scraping platforms that prohibit it.** Adapters use official partner APIs or
  the user's own authenticated session against their own account. Nothing else.
- **International coverage** in v1. US only.

## 4. Users

**Primary:** A golfer who plays 2–6 rounds a month, belongs to one or two
clubs or muni systems, and also books public rounds opportunistically.

**Secondary:** A group organizer coordinating a foursome who needs to compare
availability and price across a metro area.

## 5. Core requirements

### 5.1 Search

| ID | Requirement |
|----|-------------|
| S1 | Accept a US ZIP code or a lat/long as the search origin |
| S2 | Return results banded at 5, 10, 15, 20, 25, and 35 miles |
| S3 | Accept date (single or range), player count (1–4), and a time window |
| S4 | Return partial results when a provider is slow or down, never block on the slowest |
| S5 | Each result carries: course, tee time, price, player slots, provider, deep link, distance |

### 5.2 Coverage tiers

| Tier | Source | Auth | Ships |
|------|--------|------|-------|
| 1 | GolfNow / TeeOff / EZLinks | Partner API (OAuth2, app-level) | v1 |
| 1 | Other partner-API marketplaces | Partner API | v1 |
| 2 | Member/private club tee sheets | User's own credentials, local only | v1 |
| 3 | Course-direct engines without partner access | — | Out of scope |

Tier 1 is the coverage backbone. Tier 2 exists because private, semi-private,
and resident-rate muni inventory is invisible to every aggregator — it is the
only way to see tee times the user is uniquely entitled to.

### 5.3 Normalization

| ID | Requirement |
|----|-------------|
| N1 | Resolve the same physical course appearing under multiple providers into one entity |
| N2 | Match on geo proximity under 200m plus normalized name similarity |
| N3 | When one course has listings from several providers, show all prices with source attribution — the user decides |
| N4 | Never silently drop a listing during dedupe; collapse into a group |

## 6. Key design decisions

**One query, six bands.** Querying each radius separately would be six round
trips per provider for data that is a strict superset at 35 miles. Query once at
max radius, compute haversine distance per result, bucket client-side. Cuts
rate-limit consumption by ~6x.

**Offline ZIP resolution.** ZIP centroids come from the Census ZCTA gazetteer,
built into a local SQLite file. No geocoding API means no key, no rate limit,
no network dependency on the hot path, and no third party learning where users
are searching.

**Provider adapters are the only place platform-specific logic lives.** Auth
model, endpoint shape, and rate limits are adapter concerns. Adding a platform
means writing one class, not touching the pipeline.

**Fail soft.** A dead provider degrades coverage, not the request. Every adapter
runs behind a timeout and a circuit breaker, and the response reports which
providers answered so results are never silently incomplete.

## 7. Credential handling

This is the highest-risk part of the system and the design is deliberately
conservative.

- Credentials live in the OS keychain on the user's machine (macOS Keychain,
  libsecret, or Windows Credential Manager) via `keyring`.
- The skill runs locally. Credentials are read at invocation, held in memory for
  the life of the request, and passed to the containerized adapter as
  environment variables scoped to that process.
- Nothing is written to the image, to `docker-compose.yml`, to disk, or to shell
  history.
- Session tokens are cached in memory with a short TTL to avoid re-authenticating
  on every search.
- **There is no hosted mode.** Operating this as a service would make
  ArchBadger500 a credential custodian for accounts holding saved payment
  methods. That liability is not worth the convenience.

Users are told plainly, at credential-setup time, that automated access may
violate a platform's terms and that the risk of account action is theirs.

## 8. Success criteria

- A ZIP-code search returns results from at least two providers in under 6
  seconds at p95
- Dedupe correctly merges a course listed on both a marketplace and its own
  portal, in manual spot checks across 20 metro areas
- Zero credentials present anywhere outside the OS keychain, verified by
  grepping the image, compose file, logs, and process environment

## 9. Open questions

- Which platforms does the user actually hold accounts on? Drives tier-2 adapter
  priority.
- Is GolfNow partner approval obtainable for this use case, or does the affiliate
  path (revenue share on bookings) fit better?
- Does a saved-search / alerting feature belong in v2, given that desirable tee
  times get taken within minutes of release?
