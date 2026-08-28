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

The CLI's default output is a finished terminal tee sheet (rendered with
rich): a header panel, then one table of courses ordered by distance with
section breaks at each band edge, colored when a human runs it directly.
**Present it verbatim inside a fenced code block** — do not reformat it into
prose or a markdown table; the render is the product. After the block, add
at most one or two sentences of commentary (the standout slot, a coverage
gap, a price worth noticing).

Courses appear once each with the cheapest listing; alternatives on other
platforms show as an inline note, since the user may prefer to book where
they hold a membership or rewards balance. A yellow price means the platform
didn't state per-player-with-cart. `--plain` produces a bare table for
narrow contexts, and `--json` raw data — reach for those only when the tee
sheet genuinely doesn't fit the medium.

**Always report incomplete coverage.** The output's footnote line flags
connections that failed or timed out — keep that line intact and repeat the
warning in the commentary. A golfer who thinks they've seen everything and
hasn't will book the wrong thing. The same footnote area marks demo data;
never present demo results without saying they're fictional.

## Booking

Deep-link, don't automate. Give the user the platform URL from the result and
let them complete the purchase themselves. Automating checkout against a saved
payment method is both a terms violation and a good way to buy the wrong tee
time.

## The watcher: proactive checks with iMessage delivery

The watcher searches on the user's behalf — no chat session involved — and
texts them a digest when tee times appear for their usual play days. Offer to
set it up during install, right after platforms are connected.

Setup is a short interview. Ask conversationally (days they usually play,
usual tee-off window, usual group size, home ZIP, how far ahead their courses
open booking, the phone number or Apple ID email for iMessage — their own
number texts their self-thread), then save the answers non-interactively:

```bash
python scripts/prefs.py init --days sat,sun --window 06:30-11:00 \
  --players 4 --origin 47714 --lead-days 7 --imessage-to +15551234567
python scripts/watch.py --dry-run          # show the user what it would send
python scripts/watch.py --test-message     # confirm iMessage delivery works
python scripts/watch.py --install-launchd  # schedule the daily check
```

How it behaves, so questions can be answered accurately:

- It runs daily (launchd, at `run_at` in prefs) and checks each usual play
  day once it comes within `lead_days` — anchored to when booking windows
  open, because that's the morning good slots exist.
- It is quiet by default: a message goes out only when the tee sheet first
  opens or new times appear since the last check. Never on "still nothing."
- Delivery is via the user's own Messages.app (macOS may show a one-time
  Automation permission prompt — the user must approve it). With no
  recipient configured, it falls back to a macOS notification.
- One search per watched date per day — indistinguishable from the user
  checking manually. Never tighten this into a polling loop; that is how
  member accounts get suspended.
- `python scripts/watch.py --uninstall-launchd` turns it off;
  `prefs.py show` / `prefs.py init` review and change settings.
- Preferences live in `~/.config/teetime/prefs.json` (not secrets); the
  watcher's log is `~/.config/teetime/logs/watch.log`.

When a user mentions getting a watcher text, re-run the search fresh before
discussing it — the text is a snapshot of volatile inventory.

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
