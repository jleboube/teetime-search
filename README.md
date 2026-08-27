# Tee Time Search — a Claude Skill

A Claude Skill that searches golf tee times across booking platforms and
presents them banded by distance (5/10/15/20/25/35 miles) from a ZIP code or
location. Ask Claude *"where can I play Saturday morning near 47714?"* and get
back a merged, deduplicated, price-compared list of what's actually open.

```
Within 5 miles
   9:22a  Willow Creek Golf Club              $29  4 slots
   7:02a  Stonebridge Links                   $33  4 slots

Within 10 miles
   9:50a  Eagle Crest North                   $65  4 slots
...
```

## How it works

The skill has two halves, and the split is deliberate:

- **`teetime-search/`** — the skill itself: `SKILL.md`, a search CLI, and a
  credential broker that runs natively on the host so it can reach the OS
  keychain (containers can't).
- **`teetime-search/service/`** — a Dockerized FastAPI aggregator bound to
  `127.0.0.1` only. Provider adapters fan out concurrently, results are
  deduplicated (the same course often appears on several platforms under
  slightly different names), banded by distance, and cached for 90 seconds.

ZIP resolution is fully offline — a SQLite database of ~33,000 Census ZCTA
centroids is built into the image, so no geocoding API, no rate limits, and no
third party learning where you search.

## Install

Requires Docker and Python 3.10+.

1. Copy `teetime-search/` into your skills directory
   (`~/.claude/skills/teetime-search` for Claude Code), or point Claude at
   this repo.
2. `pip install -r teetime-search/requirements.txt`
3. `docker compose -f teetime-search/service/docker-compose.yml up -d --build`

Then ask Claude about tee times, or run a search directly:

```bash
cd teetime-search
python scripts/search.py --origin 47714 --date tomorrow --players 4 --demo
```

## Demo mode vs. live data

Out of the box the skill has **no live inventory source** — that's honest, not
broken. `--demo` enables a synthetic provider with deterministic, clearly
fictional inventory so you can see the whole pipeline work.

For real data:

- **GolfNow / TeeOff** (the largest public US inventory) requires
  [partner API credentials](https://www.golfnow.com/business-partnership).
  The adapter is written but **unverified against their sandbox** — expect to
  correct endpoint paths and field names when credentials arrive. See
  `teetime-search/references/providers.md`.
- **Tier-2 platforms** (Chronogolf/Lightspeed, foreUP, Teesnap) use your own
  member login, stored only in your OS keychain via
  `python scripts/creds.py set <provider>`. Automated access with your own
  account may violate a platform's terms of service; the tool tells you so
  and makes you confirm before storing anything.

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
