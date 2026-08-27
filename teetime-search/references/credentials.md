# Credential handling

Read this before answering a user's questions about how their logins are
handled. They deserve a specific answer, not reassurance.

## Where credentials live

The OS keychain on the user's own machine — macOS Keychain, libsecret on Linux,
Windows Credential Manager — accessed through the `keyring` library under the
service name `teetime-search`.

They are not in the container image, not in `docker-compose.yml`, not in `.env`,
not in a config file, not in shell history, and not in any log.

## The path a credential takes

1. `creds.py set <provider>` prompts for it and writes it to the OS keychain.
   Secrets are read with `getpass`, so they never appear on screen or in
   scrollback.
2. At search time, `search.py` — running natively on the host — reads the
   configured providers out of the keychain into memory.
3. They go into the POST body to `127.0.0.1:8077`, which is loopback-bound in
   `docker-compose.yml`. The service is not reachable from another machine.
4. The adapter uses them for that request, caches the resulting session token
   in memory for 15 minutes, and holds nothing else.
5. The process exits. Nothing was written.

## Why the broker runs outside Docker

Containers cannot reach the host OS keychain. That single constraint shapes the
architecture: the credential broker stays native, the adapters stay
containerized, and credentials cross that boundary only as a request body over
loopback for the duration of one search.

The alternative — mounting a secrets file into the container — would mean
credentials at rest in a file, which is exactly what this design avoids.

## Why there is no hosted mode

Running this as a service for other people would mean storing their credentials
in a form that can be replayed, which means reversibly. One encryption key would
then stand between a breach and every user's golf account — accounts that
routinely carry a saved payment method.

For an LLC operated by one person, that is a liability with no matching upside.
The local-only design is not a v1 limitation to be removed later; it is the
reason the tier-2 feature is defensible at all.

## What to tell a user before they store anything

`creds.py` prints this, but if it comes up in conversation, the honest version
is short:

- Automated access may violate the platform's terms of service.
- Account suspension is a real possibility and the risk is theirs.
- The tool reads availability only and never completes a booking.

Don't soften it. A user who gets their club account suspended because the
warning was buried has a legitimate grievance.

## Verifying the claims

These are testable, and worth testing after any change to the compose file or
Dockerfile:

```bash
# Nothing credential-shaped baked into the image
docker run --rm --entrypoint sh teetime-api -c \
  'grep -ri "password\|secret\|token" /srv/app || echo "clean"'

# Service is loopback-only
docker compose port api 8000        # expect 127.0.0.1:8077

# Credentials absent from container environment
docker compose exec api env | grep -i "pass\|secret" || echo "clean"
```

If any of those stop being true, the posture described above is no longer
accurate and this document needs correcting rather than the claim being kept.
