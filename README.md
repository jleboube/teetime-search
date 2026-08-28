# Tee Time Search — a Claude Skill

A Claude Skill that searches golf tee times across the booking platforms
**you connect with your own accounts** — foreUP, Chronogolf/Lightspeed,
Teesnap — and presents them merged, deduplicated, and banded by distance
(5/10/15/20/25/35 miles) from a ZIP code or location. Ask Claude *"where can I
play Saturday morning near 47714?"*; it asks for whatever's missing (date,
group size, location), fans out across your connections, and compares what's
actually open.

```
╭────────────────────────────────────────────────────────╮
│ ⛳ Tee Sheet  Saturday Aug 29 · 4 players · near 47714 │
│ 12 courses · 128 open seats · within 35 mi             │
╰────────────────────────────────────────────────────────╯
╭──────┬─────────┬─────────────────────────────┬──────────┬───────┬────────╮
│   mi │ tee off │ course                      │ $/player │ slots │ via    │
├──────┼─────────┼─────────────────────────────┼──────────┼───────┼────────┤
│  2.0 │   9:22a │ Willow Creek Golf Club      │      $29 │   4   │ foreup │
│  4.9 │   7:02a │ Stonebridge Links           │      $33 │   4   │ foreup │
├──────┼─────────┼─────────────────────────────┼──────────┼───────┼────────┤
│  7.8 │   9:50a │ Eagle Crest North           │      $65 │   4   │ chrono │
╰──────┴─────────┴─────────────────────────────┴──────────┴───────┴────────╯
```

Full color in a real terminal (rich), with section breaks at each distance
band. And you don't even have to ask: **the watcher** knows which days you
usually play and texts you an iMessage digest the morning your courses'
booking windows open.

## Just want to use it?

You need a Mac with [Claude Code](https://claude.com/claude-code) on it —
nothing else. Claude does the entire setup. Open Terminal (Cmd+Space, type
"Terminal", Enter) and paste:

```
git clone https://github.com/jleboube/teetime-search ~/teetime-search && cd ~/teetime-search/teetime-search && claude
```

(If your Mac offers to install "command line developer tools" at any point,
click Install — that's normal.) When Claude starts, paste this and let it
drive:

> Read SKILL.md in this folder. Install this folder as a skill at
> ~/.claude/skills/teetime-search, install whatever it needs, start the
> service, and show me a demo tee sheet so I can see it working. Then walk
> me through connecting my golf booking accounts, and set up the watcher so
> it texts me when tee times open for the days I usually play.

Claude will ask about your usual play days, group size, ZIP code, and phone
number as it goes — answer in plain English. The step-by-step version of
this, written for non-technical folks, is
[**START_HERE.md**](teetime-search/START_HERE.md).

Searching works on macOS and Linux; the watcher's iMessage delivery and
scheduling are macOS-only.

## How it works

The skill has two halves, and the split is deliberate:

- **`teetime-search/`** — the skill itself: `SKILL.md`, a search CLI, and a
  credential broker that runs natively on the host so it can reach the OS
  keychain (containers can't).
- **`teetime-search/service/`** — a FastAPI aggregator bound to `127.0.0.1`
  only, run as a plain local process (Docker optional). Provider adapters
  fan out concurrently, results are deduplicated (the same course often
  appears on several platforms under slightly different names), banded by
  distance, and cached for 90 seconds.

ZIP resolution is fully offline — a SQLite database of ~33,000 Census ZCTA
centroids, downloaded once at first start, so no geocoding API, no rate
limits, and no third party learning where you search.

## Install (developers)

Copy `teetime-search/` into your skills directory
(`~/.claude/skills/teetime-search` for Claude Code) or work from the clone.
There is nothing to pip-install: every script self-bootstraps a managed venv
at `~/.config/teetime/venv` on first run (PEP 668 makes bare `pip install`
fail on modern Macs anyway, so no instruction here would survive one). The
first search also starts the aggregator as a local process and downloads the
ZIP database — one-time, a couple of minutes. Python 3.10+ (`python3
--version` to check). Prefer containers? The same service runs with
`docker compose -f teetime-search/service/docker-compose.yml up -d --build`.

Ask Claude about tee times, or run a search directly:

```bash
cd teetime-search
python3 scripts/search.py --origin 47714 --date tomorrow --players 4 --demo
```

## Connecting your booking platforms

Coverage is whatever you choose to connect. Each connection uses your own
login for that platform, stored only in your OS keychain:

```bash
cd teetime-search
python scripts/creds.py set foreup      # your foreUP courses
python scripts/creds.py set chronogolf  # your Lightspeed Golf club
python scripts/creds.py set teesnap     # your Teesnap club
```

The broker prompts for exactly what each platform needs and requires you to
acknowledge, first, that automated access with your own account may violate
that platform's terms of service — that risk is yours to accept or decline.

**Verify each connection on first use.** None of these platforms publish an
official user API, so each adapter is modelled on the API behind the
platform's own booking pages. Run one search after connecting and compare it
with the club's booking site. foreUP is the most complete; Teesnap is a
documented placeholder awaiting someone with a club login to capture the real
endpoints (`teetime-search/service/app/providers/teesnap.py` explains how).

`--demo` enables a synthetic provider with deterministic, clearly fictional
inventory so you can see the whole pipeline work before connecting anything.

Optionally, operators who obtain
[GolfNow partner credentials](https://www.golfnow.com/business-partnership)
can also light up public discovery inventory — see
`teetime-search/references/providers.md`.

## The watcher: tee times come to you

Nobody opens a terminal to book golf — so after setup you shouldn't have to.
Tell it your pattern once:

```bash
cd teetime-search
python scripts/prefs.py init          # days you play, group size, ZIP, phone
python scripts/watch.py --dry-run     # preview what it would send
python scripts/watch.py --install-launchd
```

From then on, a daily launchd job checks each of your usual play days as soon
as it enters your courses' booking window and iMessages you a digest — via
your own Messages.app, to your own number (your self-thread works great):

> ⛳ Sat Aug 29: tee sheet is open — 12 courses near 47714 for 4
> • 7:26a Willow Creek Golf Club $29 (2 mi)
> • 8:06a Stonebridge Links $33 (5 mi)
> …and 10 more

It's quiet by default (messages only when the sheet opens or new times
appear), checks once per day per date — the same footprint as checking by
hand — and `--uninstall-launchd` turns it off.

## Security posture

- Credentials live in the OS keychain only — never on disk, never in the
  image, never in compose files or logs.
- The API binds to loopback only; it receives credentials in request bodies
  and must not be reachable off-host.
- The skill never completes a booking. It deep-links and hands off.
- Failed providers are always reported — partial results are never presented
  as complete.

See `teetime-search/references/credentials.md` for the full posture and
verification commands.

## Development

```bash
cd teetime-search/service
python -m pytest tests/          # dedupe regression suite
```

`PRD.md` covers the product requirements; `CLAUDE.md` is the working handoff
doc (invariants, gotchas, phased plan).

## License

MIT — see [LICENSE](LICENSE).
