# Official Garmin Connect Personal API backend

Status: implemented behind `GARMIN_AUTH_MODE`, off unless a key is configured.
Scope: read-only. Writes stay on the SSO backend.

## Why

Until 2026-03 this project could only talk to Garmin by logging into the same
private SSO endpoint the Garmin Connect web app uses, then reusing the resulting
tokens. That login is undocumented, unversioned and unfriendly to automation,
and it is the root cause of most of the open bug reports:

| Issue | Symptom | Why the SSO backend causes it |
|---|---|---|
| #330 | `garmin-mcp-auth` says it saved tokens, the server says it cannot find them | tokens are a directory of files with no canonical location; a client that starts with a different `HOME`/`GARMINTOKENS` reads another directory |
| #366 | `Failed to retrieve social profile` on Windows | the login chain has five strategies (mobile, portal, widget, cffi, ...) and the profile fetch is a separate call that can fail on its own |
| #477 | HTTP 429 on every attempt | each retry is a fresh SSO login attempt, and Garmin rate-limits aggressively per IP |

On 2026-10-21 Garmin reopened its developer program with three tiers. The
**Personal** tier gives any individual a documented, self-service way in:

* create a key at `connect.garmin.com/app/settings/personalApiToken`
  (30/90/180/365-day expiry, shown once);
* `POST /personalapitoken-service/personal-api/personal-api-key/token` with
  `Authorization: ApiKey <key>` returns a 24-hour JWT bearer token;
* call `https://personal.connectapi.garmin.com` with `Authorization: Bearer <jwt>`.

There is no password, no MFA, no cookie jar, no token directory and no login
rate limit to trip.

## What Garmin exposes

Personal: Activity (JSON + FIT), Health (JSON), Women's Health (JSON).
Read-only, pull only.

Starter (business, free up to 1,000 users): adds the Courses and Training APIs
-- i.e. pushing routes and structured workouts to devices. That is the tier
`create_strength_workout` / `download_course_gpx`-style **writes** would need.
Enterprise (paid, from April 2027) adds Golf, historical backfill and support.

## How the backend works

The Personal gateway is not a new data model: it serves the same
`/<service>-service/...` paths the Garmin Connect web client uses, behind OAuth2
instead of the `JWT_WEB` cookie. Everything above the transport in this project
therefore keeps working unchanged.

```
           +---------------------+
 tools --> |  garminconnect.Garmin  (unchanged: get_sleep_data, get_activities, ...)
           +----------+----------+
                      | self.client
           +----------v----------+
           | personal_api        |  PersonalApiHttpClient(GarminHttpClient)
           |  - _connectapi  = https://personal.connectapi.garmin.com
           |  - di_token     = <jwt from the key>
           |  - read_only    = True
           +----------+----------+
                      |
           +----------v----------+
           | PersonalApiAuthenticator |  24h JWT, cached, re-exchanged at 1h left
           +---------------------+
```

`PersonalApiHttpClient` subclasses `garminconnect.client.Client`, the library's
transport. Every high-level method in the library (`get_sleep_data`,
`get_activities`, `get_training_status`, ...) builds its URL from
`self._connectapi` and its headers from `get_api_headers()`, so pointing those
at the gateway switches the entire library without touching a single call site
in this server.

Three behaviours are overridden:

* `get_api_headers()` -- sends only `Accept` and `Authorization: Bearer ...`,
  matching Garmin's own Personal client, instead of the Android-impersonation
  headers the SSO backend needs.
* `_refresh_di_token()` -- Personal tokens have no refresh token, so
  re-exchange the API key. This is also what the parent's 401 retry calls.
* `_run_request()` -- refuses every non-GET locally with
  `PersonalApiReadOnlyError`, so a write tool reports "the Personal tier is
  read-only, use GARMIN_AUTH_MODE=sso" instead of surfacing a gateway 403.

Token handling:

| Concern | Behaviour |
|---|---|
| Lifetime | 24h JWT (`exp` claim), or the key's expiry if sooner |
| Cache | `~/.config/garmin/connect-personal-api-bearer-token.json`, mode 600 |
| Cache key | SHA-256 of `base_url + "\0" + key`, so rotating the key or endpoint never reuses a token |
| Refresh | re-exchange when less than 1h remains (Garmin's own window) |
| Expired key | 401/403 at the token endpoint becomes an error telling the user to generate a new key |
| Rate limit | 429 with `Retry-After` surfaced verbatim, and the key is not blamed |

## Startup paths

`GARMIN_AUTH_MODE`:

* `auto` (default) -- use the Personal backend when a key is configured,
  otherwise SSO. If the key is present but rejected, log the failure and fall
  back to SSO for that session, so an expired key degrades instead of
  bricking the server.
* `personal` -- never contact SSO. A missing or rejected key is fatal: the
  server starts with no client and every tool reports the real reason.
* `sso` -- never read a key. This is the pre-2026-10 behaviour and remains the
  only way to write.

## Capability matrix

Garmin documents the *data areas*, not the *paths* the gateway proxies, and
this server has ~150 tools built on internal paths. `scripts/probe_personal_api.py`
resolves this empirically per key: it calls one representative method per area
and prints the outcome, grouped by area, with an optional JSON report.

Expected shape, from Garmin's documentation and its own client:

| Area | Personal tier | Notes |
|---|---|---|
| activities, activity details, FIT download | yes | `activitylist-service`, `activity-service`, `download-service` |
| sleep, sleep summaries | yes | `sleep-service`, `wellness-service` |
| daily summary, steps, stress, floors, intensity minutes | yes | `usersummary-service` |
| heart rate, HRV, SpO2, respiration, body battery | yes | `wellness-service`, `hrv-service` |
| body composition, weight, blood pressure | yes | `weight-service`, `bloodpressure-service` |
| training status, readiness, VO2 max, endurance, hill, race predictions | probably | documented as "Health"; `metrics-service` is the same family |
| devices, primary training device | probably | `device-service` is in the same family, not documented |
| workouts, scheduled workouts, training plans | read only? | Courses/Training APIs are Starter; reading may still work |
| courses list / GPX | read only? | the Course **push** API is Starter-gated; GET may pass |
| gear, badges, challenges, goals, nutrition settings, calendar | unknown | internal services, may be blocked |
| every write tool | **no** | Starter tier or SSO only |

Run the probe and replace "probably/unknown" with what your key actually
returns. If an area matters to you and the probe says no, the answer is the
Starter tier (`garmin.com/en-US/forms/StarterGarminConnectDeveloperAccess`), not
a workaround here.

## Limitations and non-goals

* **No writes.** Deliberate: the tier does not allow them.
* **No backfill.** Personal has no historical-data endpoint; range endpoints
  are limited to what each service returns.
* **No display name fallback chain beyond two paths.** `_read_profile()` tries
  `/userprofile-service/userprofile/settings` (documented) and
  `/userprofile-service/socialProfile` (what `Garmin.login()` uses). Many tools
  embed the display name in the URL, so a key whose profile cannot be read
  produces a startup warning rather than silent 403s later.
* **Not a replacement for the business Starter tier.** If RunCoach-style course
  or workout push is the goal, this backend reads the data; the push still
  needs an OAuth2 app registration.

## Rolling back

Unset `GARMIN_PERSONAL_API_KEY`, or set `GARMIN_AUTH_MODE=sso`. Nothing about
the SSO path changed, including the `~/.garminconnect` token store.
