---
name: teetime-search
description: Searches available golf tee times across booking platforms near a ZIP code or shared location, banded by distance (5/10/15/20/25/35 miles). This skill should be used whenever the user mentions tee times, booking a round of golf, golf availability, finding a course to play, GolfNow, TeeOff, or asks anything like "where can I play Saturday morning" or "what's open near me this weekend" — even without the words "tee time." Also applies when the user wants to compare green fees across courses or check their own club's member tee sheet.
---

# Tee Time Search

Searches golf tee time inventory across every configured provider, merges the
results, and presents them banded by distance from the user's location.

All commands below run from this skill's base directory (the directory
containing this SKILL.md).

## Setup (first use)

Two prerequisites, checked in order:

1. **Host dependencies** for the CLI scripts:

   ```bash
   pip install -r requirements.txt
   ```

2. **The aggregator service** must be running (requires Docker):

   ```bash
   docker compose -f service/docker-compose.yml up -d --build
   ```

   The first build downloads the Census ZIP gazetteer (~2 MB) and takes a
   minute or two. Verify with
   `curl -s http://127.0.0.1:8077/health` — expect `{"status":"ok"}`.

Out of the box no live provider is configured — GolfNow requires partner API
credentials (see `references/providers.md`). To exercise the full pipeline
with clearly-labeled fictional inventory, add `--demo` to any search. Tell the
user plainly when results are demo data.

## When more is needed from the user

The search needs an origin, a date, and a player count. If any are missing, ask
for them together in one message rather than one at a time — a golfer asking
about tee times is usually mid-plan and doesn't want an interrogation.

Sensible defaults when the user is vague:
- No date → today if it's before noon, otherwise tomorrow
- No player count → 4 (the most common group, and it's the most restrictive
  filter, so a 4-player search never shows a slot the group can't actually take)
- No time window → the whole day
- No origin → ask; never guess

## Running a search

```bash
python scripts/search.py \
  --origin 47714 \
  --date 2026-08-29 \
  --players 4 \
  --window 07:00-11:00
```

`--origin` accepts a 5-digit US ZIP or a `lat,lng` pair. Add `--max-radius` to
change the outer band (default 35). Add `--json` for raw output instead of the
formatted table. Add `--demo` to include the synthetic demo provider.

## Presenting results

Golfers scan for three things: how far, what time, how much. Lead with those.

Group by distance band, nearest first, and inside each band sort by tee time.
Skip empty bands entirely rather than printing "no results" six times.

```
Within 10 miles
  7:42a   Cambridge Golf Course        $38   4 slots   GolfNow
  8:10a   Fendrich Golf Course         $29   2 slots   foreUP (your account)
  9:05a   Helfrich Hills               $34   4 slots   GolfNow

Within 25 miles
  7:20a   Rolling Hills CC             $45   4 slots   Chronogolf (member)
```

When the same course shows up from more than one provider, show it once with
the cheapest price, and mention the alternative inline — the user may prefer to
book somewhere they have a rewards balance:

```
  8:10a   Fendrich Golf Course         $29   2 slots   foreUP (also $34 on GolfNow)
```

**Always report incomplete coverage.** The response includes a `providers`
block listing which ones answered. If any failed or timed out, say so in one
line after the results. A golfer who thinks they've seen everything and hasn't
will book the wrong thing.

## Booking

Deep-link, don't automate. Give the user the provider URL from the result and
let them complete the purchase themselves. Automating checkout against a saved
payment method is both a terms violation and a good way to buy the wrong tee
time.

## Credentials

Tier-2 providers — private clubs, member portals, resident-rate muni systems —
need the user's own login. These are optional; the skill works without them,
just with less coverage.

To set one up:

```bash
python scripts/creds.py set chronogolf
```

This prompts for credentials and stores them in the OS keychain. Nothing is
written to disk, to the container image, or to the compose file.

`python scripts/creds.py list` shows which providers are configured.
`python scripts/creds.py rm <provider>` removes one.

Before helping a user configure credentials for the first time, tell them
plainly that automated access may violate the platform's terms of service and
that account suspension is a real possibility. They should decide knowing that.
Read `references/credentials.md` for the full posture if they ask questions
about how their credentials are handled — they deserve a straight answer.

## Adding a provider

Read `references/providers.md`. It covers the adapter interface, the auth models
each platform uses, and what access to arrange before an adapter can work at
all. Most of the effort in adding a platform is obtaining access, not writing
code.

## Things that will bite you

**Tee time inventory is extremely volatile.** Desirable slots disappear within
minutes. Results are cached for 90 seconds; anything older is misleading. If a
user comes back to a previous search, re-run it rather than reusing the output.

**Price fields are inconsistent across providers.** Some quote per player, some
per group, some exclude cart. The service normalizes to per-player-with-cart
where it can determine it, and flags `price_confidence: low` where it can't.
Surface that flag rather than presenting an uncertain number as fact.

**Distance is straight-line, not driving.** A course 12 miles away across a
river may be a 40-minute drive. Don't present the bands as travel time.
