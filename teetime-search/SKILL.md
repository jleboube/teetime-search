---
name: teetime-search
description: Searches golf tee times across the booking platforms the user has connected with their own accounts (foreUP, Chronogolf/Lightspeed, Teesnap, and others), merged and banded by distance (5/10/15/20/25/35 miles) from a ZIP code or location. This skill should be used whenever the user mentions tee times, booking a round of golf, golf availability, finding a course to play, GolfNow, TeeOff, or asks anything like "where can I play Saturday morning" or "what's open near me this weekend" — even without the words "tee time." Also applies when the user wants to compare green fees across courses, check their club's member tee sheet, or connect a golf booking account.
---

# Tee Time Search

Searches tee time inventory across every booking platform the user has
connected, merges the results, and presents them banded by distance from the
user's location.

The model is connection-based: the user decides which schedulers, providers,
and bookers to link, using their own accounts. Each connection adds coverage;
none is required for the skill to load, but at least one (or demo mode) is
required before a search can return anything.

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

## Starting a search: gather the essentials first

A search needs three things: **a date, the total number of golfers in the
group, and an origin** (ZIP or location). When the user initiates the skill
without providing them, ask for all missing pieces together in one short
message — a golfer planning a round doesn't want an interrogation. A good
opening when everything is missing:

> "What day are you looking to play, how many golfers total, and what ZIP
> should I search around?"

Also check which platforms are connected (`python scripts/creds.py list`). If
none are, say so in the same message and offer to set up a connection — an
empty search with no connections is a dead end the user should see coming.

Fill in, rather than ask about, what the user has already implied:
- "Saturday morning" → the coming Saturday, window 06:00–12:00
- "me and my brothers" plus context → count them; when genuinely unclear, ask
- No time window mentioned → the whole day (don't ask)
- Player count given as "a foursome" → 4

Player count matters more than it looks: it is the most restrictive filter, so
searching for the full group size guarantees every slot shown can actually
hold the group.

## Connecting platforms

Each connection uses the user's own login for that platform, stored in the OS
keychain by the credential broker:

```bash
python scripts/creds.py set foreup      # public + member courses on foreUP
python scripts/creds.py set chronogolf  # Lightspeed Golf clubs
python scripts/creds.py set teesnap     # Teesnap clubs
```

The broker prompts for exactly the fields that platform needs (login, plus
club/course identifiers — the user can read them from their club's booking
URL; help them find these if they're unsure).
`python scripts/creds.py list` shows current connections;
`python scripts/creds.py rm <provider>` disconnects one.

Two things to tell the user honestly during setup:

1. **Terms of service.** Automated access with their own account may violate
   the platform's terms; account suspension is a real possibility. The broker
   states this and requires confirmation before storing anything. Don't
   soft-pedal it.
2. **First-use verification.** These platforms publish no official API, so
   each adapter is modelled on observed behavior. After connecting a platform,
   run one search and have the user compare it against the club's own booking
   page before trusting the results. If it errors or looks wrong, read the
   adapter's docstring in `service/app/providers/` — it documents how to
   correct the endpoints. The Teesnap adapter in particular is a placeholder
   that needs this on first connection.

For how each platform's access model works (and how to add a new platform),
read `references/providers.md`. For how credentials are protected, read
`references/credentials.md` — users who ask deserve a straight answer.

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
formatted table. Add `--demo` to include the synthetic demo provider —
clearly-fictional inventory for trying the pipeline before connecting
anything; always tell the user when results are demo data.

## Presenting results

Golfers scan for three things: how far, what time, how much. Lead with those.

Group by distance band, nearest first, and inside each band sort by tee time.
Skip empty bands entirely rather than printing "no results" six times.

```
Within 10 miles
  7:42a   Cambridge Golf Course        $38   4 slots   foreUP (your account)
  9:05a   Helfrich Hills               $34   4 slots   foreUP (your account)

Within 25 miles
  7:20a   Rolling Hills CC             $45   4 slots   Chronogolf (member)
```

When the same course shows up from more than one platform, show it once with
the cheapest price, and mention the alternative inline — the user may prefer
to book where they hold a membership or rewards balance:

```
  8:10a   Fendrich Golf Course         $29   2 slots   foreUP (also $34 on GolfNow)
```

**Always report incomplete coverage.** The response includes a `providers`
block listing which connections answered. If any failed or timed out, say so
in one line after the results. A golfer who thinks they've seen everything and
hasn't will book the wrong thing.

## Booking

Deep-link, don't automate. Give the user the platform URL from the result and
let them complete the purchase themselves. Automating checkout against a saved
payment method is both a terms violation and a good way to buy the wrong tee
time.

## Things that will bite you

**Tee time inventory is extremely volatile.** Desirable slots disappear within
minutes. Results are cached for 90 seconds; anything older is misleading. If a
user comes back to a previous search, re-run it rather than reusing the output.

**Price fields are inconsistent across platforms.** Some quote per player, some
per group, some exclude cart. The service normalizes to per-player-with-cart
where it can determine it, and flags `price_confidence: low` where it can't.
Surface that flag rather than presenting an uncertain number as fact.

**Distance is straight-line, not driving.** A course 12 miles away across a
river may be a 40-minute drive. Don't present the bands as travel time.
