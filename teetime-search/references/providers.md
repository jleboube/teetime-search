# Provider reference

The adapter code for a platform takes an afternoon. Getting access takes weeks
to never. Read this before estimating any coverage expansion.

## Access landscape

| Platform | Owner | Access | Radius search? | Effort |
|---|---|---|---|---|
| GolfNow / TeeOff / EZLinks | NBC Sports Next | Affiliate & Partner API, OAuth2, sandbox | Yes | Application + review |
| Lightspeed Golf (Chronogolf) | Lightspeed | Partner API v2, hand-provisioned | No — club-scoped | Email, per-club onboarding |
| foreUP | foreUP | Partner APIs, existing customers only | No | Effectively closed |
| Teesnap | Teesnap | No public API | No | Closed |
| Club Prophet | Club Prophet | No public API | No | Closed |
| Supreme Golf | — | Aggregator itself, no public API | — | Closed |
| Quick18 | — | No public API | No | Closed |

**The practical consequence:** there is no partner-API route to broad
coverage, which is why this skill is connection-based instead. Coverage is the
set of platforms the user links with their own accounts — their foreUP clubs,
their Lightspeed club, their Teesnap club — plus, optionally, GolfNow partner
credentials for public discovery inventory if the operator obtains them. A
product promising "all major platforms" out of the box would be lying; one
that grows with each account the user connects is honest and still useful.

## GolfNow Affiliate & Partner API

Apply at `golfnow.com/business-partnership`. They review the use case before
granting credentials, so lead with what you're building and how it drives
bookings to their inventory. Two paths exist: affiliate (revenue share on
bookings you originate) and partner (deeper integration). For a search product
that hands off booking, affiliate is usually the faster approval.

The API is REST/JSON over OAuth 2.0 with a sandbox for live testing.

**The endpoint paths and field names in `golfnow.py` are placeholders.** They
follow the documented shape but are not verified. Check every one against the
sandbox and fix the adapter before trusting a single result.

## Lightspeed Golf / Chronogolf Partner API

Email `golf.api@lightspeedhq.com` for credentials. There is no self-service
developer portal.

Two things will cost you time if you don't know them going in:

1. **Clubs are onboarded individually.** Access to the API does not mean access
   to every Lightspeed club. Each club must be added to your integration, which
   generally requires that club's consent. This is why it's modelled as a
   tier-2 member source rather than a discovery source.
2. **Use V2. V1 is deprecated** and gets no new endpoints or fixes. The ids are
   not interchangeable — a V1 integer id returns 404 against a V2 endpoint and
   vice versa. V2 uses UUIDs and JSON:API envelopes (`data` / `included` /
   `meta`), which is why the adapter resolves club attributes out of `included`.

## Writing a new adapter

Subclass `TeeTimeProvider` (public inventory) or `CredentialedProvider`
(tier-2), implement `search()`, and register it in `providers/registry.py`.

```python
class MyProvider(TeeTimeProvider):
    name = "myprovider"
    auth_model = AuthModel.PARTNER_API
    timeout_s = 6.0

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("api_key"))

    async def search(self, req: SearchRequest) -> list[TeeTime]:
        ...
```

Four rules that keep the pipeline honest:

- **Return the full radius**, not a band. Banding happens once, upstream.
- **Never swallow an error into an empty list.** Raise `ProviderError`. The
  base class converts it into a `ProviderStatus` the user sees. An adapter that
  returns `[]` on failure makes an outage look like "no tee times available,"
  which is worse than an error.
- **Set `price_confidence` honestly.** If you can't tell whether a rate is per
  player or includes a cart, mark it `LOW`. The renderer flags it rather than
  presenting a guess as fact.
- **`enabled` must be false without access.** A misconfigured adapter should
  drop out of the fan-out, not fail every search with the same error.

## User-account adapters (foreUP, Teesnap)

`foreUP`, `Teesnap`, and `Club Prophet` have no partner access available to an
independent developer; their inventory is reachable only through the APIs
behind their customer-facing booking pages. The defensible way to use those is
the one implemented here: a credentialed adapter acting as the user, against
the user's own account, running locally, with the ToS risk stated before
credentials are stored (`creds.py` does this). Never build these as general
discovery sources — that shifts from "the user automating their own account"
to "scraping the platform," which is a different thing legally and ethically.

State of each:

- **`foreup.py`** — modelled on the JSON API foreUP's own booking pages call
  (login for a JWT, then a `times` query per schedule). Undocumented, so
  verify on first connection: run a search and compare against the club's
  booking page. The `course_id`/`schedule_id` pair comes from the club's
  booking URL: `foreupsoftware.com/index.php/booking/{course_id}/{schedule_id}`.
- **`teesnap.py`** — a placeholder. Teesnap's browser API hasn't been mapped
  even informally; the adapter documents exactly how to complete it (log into
  the club subdomain with dev tools open, capture the login and tee-sheet
  requests, correct the paths and field names).
- **Club Prophet** — not yet written; follow the same pattern.

Because no platform returns course coordinates through these APIs, the broker
collects each course's ZIP and the adapter resolves it against the local
gazetteer — centroid accuracy is within a couple of miles, fine against
5-mile bands.
