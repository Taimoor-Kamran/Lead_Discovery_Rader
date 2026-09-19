# Changelog

All notable changes, one section per merged spec. Newest first.
Format: `## [vX.Y.Z] - YYYY-MM-DD` followed by Added / Changed / Fixed.

## [v0.4.0] - 2026-09-19

### Added

- **The SSRF-guarded fetcher** (`app/core/safe_fetch.py`, `app/core/fetch_backends.py`).
  Every request to a business's own website goes through one door: `http`/`https` only,
  ports 80 and 443 only, every resolved address checked against private, loopback,
  link-local, multicast, reserved, unspecified and CGNAT ranges (IPv4, IPv6 and the
  IPv4-mapped forms), at most three redirect hops with **each hop validated again**,
  bodies streamed and cut off at 2 MB, and certificates always verified — a TLS failure is
  a recorded result, never a retry with verification off. `robots.txt` is fetched through
  the same guard and cached per host for 24 hours: 2xx is obeyed, 4xx means no rules, and
  anything else means stay away (RFC 9309's conservative reading). One host is asked at
  most once every five seconds, and the whole system keeps at most four fetches in flight.
- **The website audit engine** (`app/modules/audit_web`). One homepage, no crawling, no
  JavaScript: reachability and the redirect chain, HTTPS and the certificate, mobile
  viewport, title, meta description, `h1`, LocalBusiness JSON-LD, favicon, the *presence*
  of a phone link, email link or contact form, booking and shop signatures, linked social
  platforms, tech stack, copyright year, a JavaScript-shell flag, and PageSpeed Insights
  (mobile) for the score, LCP, CLS, TBT and CrUX category. Every check returns its value
  with the verbatim text it read and the URL it read it at; unknown stays `null`.
- **The findings catalogue**, worded as observations. Sixteen codes, each with a severity
  and the service category it points at, and a wording rule enforced over the whole
  catalogue by a test: a message must open with "Audit found", "Audit could not",
  "PageSpeed" or "Listing shows", and may never contain "needs", "should", "bad",
  "terrible" or "outdated website". Every finding carries non-empty evidence, and only
  `no_website` may omit an evidence URL — what it cites is our own business record.
- **`website_audits`** (migration `0004`), with a GIN index on `findings` for filtering by
  code. The full HTML is never stored: a SHA-256 proves whether a page changed, and the
  visible text is kept only as long as `AUDIT_CONTENT_TTL_DAYS` (90) allows.
- **The `audit` job**, queued automatically when a resolution run finishes and keyed off it
  so a retry never leaves two behind. It audits the businesses that run touched whose
  newest audit is missing or older than `AUDIT_MAX_AGE_DAYS` (30), skips businesses that
  have closed for good, and reports `{audited, skipped, robots_blocked, unreachable,
  failed, psi_calls}`. **One bad website never costs the run**: a business whose audit
  trips a bug in our own code is stored as a `failed` audit inside its own savepoint and
  the run carries on. A PageSpeed failure never fails an audit either — the audit finishes
  with `psi = null` and `checks.psi_error` saying why.
- **Endpoints.** `POST /businesses/{id}/audit` (`admin`, `tech_admin`, `reviewer`,
  `sales_rep`), `POST /jobs/{id}/audit` (`admin`, `tech_admin`),
  `GET /businesses/{id}/audits`, `GET /website-audits/{id}`, and on `GET /businesses` the
  repeatable `finding=` filter (AND, against the **newest** audit), `audit_status=` and a
  `latest_audit` on every item. `page_text` is readable by `admin`, `reviewer` and
  `tech_admin` only; other roles see `page_text_hidden: true`.
- **`pagespeed_insights` as a source row** (`kind=api`, `role=audit_service`), so its calls
  are metered in `api_calls`, rate limited and capped per day like any other. It is not a
  searchable source: a search job naming it is a 422, not a run that finds nothing.
- **Demo websites, so the audits need no network and no key.** Twenty-four checked-in sites
  under `app/demo/sites/<host>/` cover a modern site with a booking widget, an http-only
  site with a 2016 copyright, WordPress with no meta description, Wix / Square / GoDaddy
  builder sites, a Shopify store, a `robots.txt` that disallows everything, a `robots.txt`
  that answers 503, an http→https redirect chain, a host that cannot connect, a JavaScript
  shell, a certificate that does not verify, and a PageSpeed quota error.
  `expected_audits.json` records what each must produce and an integration test asserts it.
- **`make recompute-businesses`** (`python -m app.cli recompute-businesses
  [--business-id ID]`). Batched and idempotent, printing `changed / unchanged`. Needed
  whenever the survivorship rules change on a live database: existing rows still show what
  the old rules decided, and nothing else would ever revisit them.
- **README:** "How the website audit works" and "What the bot does and doesn't do" — what
  it reads, what it refuses to do, and how a site owner can opt out with one robots line.

### Changed

- **`make load-demo-data` now runs the whole pipeline.** It discovers, resolves *and*
  audits, leaving 29 businesses and 28 audits rather than two run ids to poll.
  `ARGS=--queue-only` keeps the old behaviour.
- **`make purge-expired` also expires audit page text**, and reports it. The checks, the
  findings and their evidence snippets are our own observations, so they stay.
- **A weak JWT secret now stops the process.** Under `staging` or `production` a secret
  shorter than 32 bytes (RFC 7518 §3.2) makes the API, the worker and the CLI refuse to
  start, with the fix in the message and never the secret itself; under `local`,
  `development` or `ci` it logs a warning. `.env.example` shows `openssl rand -hex 32`.
- **Two demo listings moved from `https` to `http`** (`bartoncreekplumbing.invalid` and
  `travisheightsplumbers.invalid`) so the demo covers `no_https` and an http→https
  redirect. Neither changes a domain, so every v0.3.0 resolution expectation still holds.

### Fixed

- **`make check` no longer prints `InsecureKeyLengthWarning`.** One test signed a token
  with an 18-byte secret to prove another secret is rejected; it now uses a long one.
- **Three things a hand-read audit said badly** (found by reading
  `bartoncreekplumbing.invalid` by eye; `rules_version` is now `audit-2`):
  - **`tls_valid` no longer claims a certificate verified on a page served over `http`.**
    There is no certificate to judge, so the value is `null` and the evidence says
    `not applicable: served over http`. An `https` page is unaffected, and a certificate
    that does not verify is still `false` with `tls_invalid`.
  - **A presence check now answers the same way whichever check it is.** `booking`,
    `viewport_meta`, `meta_description`, `structured_data`, `ecommerce` and `title`
    returned `null` for something absent while `favicon` and `mailto_link` returned
    `false`. The rule is now written down and tested: on a page that was fetched and
    parsed, absent is **`false`**; `null` means the check could not run.
  - **Snippet evidence is read from the page's visible text.** `copyright_year` cited a
    window cut out of the HTML that began and ended mid-tag
    (`el:+1-512-555-0102">Call ... </footer`); it now cites the line a visitor reads,
    trimmed to whole words. A signature that exists only in markup cites the whole tag it
    sits in rather than a fragment of one.

## [v0.3.0] - 2026-09-19

### Added

- **Normalization** (`app/modules/normalization`). One `normalize(candidate, source)`
  turns any source's record into a `NormalizedBusiness`: display and comparison names, a
  phonetic `name_key`, E.164 phones, structured addresses with an expanded `street_key`,
  websites split into `(website, domain, website_kind)`, an industry taxonomy, a business
  status and a precision-7 geohash. Nothing is guessed — an unparseable phone is `null`,
  a social profile has no domain, and a record that cannot be named is marked `invalid`
  with the reason rather than dropped.
- **`businesses` and `business_field_values`.** A business row is a *view*: every value
  on it is the survivorship winner among the field values beneath it, each carrying its
  source, the discovered record it came from, when it was observed and when it expires.
  Recomputing is therefore always safe, which is what makes both a merge and a retention
  purge show up correctly.
- **Entity resolution** (`app/modules/resolution`). Blocking narrows a record to the
  businesses sharing a domain, a phone, a postal code and name key, or a map cell with a
  close enough name; five weighted signals (all configurable) score each pair and are
  stored alongside the score; then the hard rules override it. A name alone never merges,
  and a pair that disagrees about both its domain and its phone is never one business.
- **The review gate.** `GET /match-candidates?status=pending` and
  `POST /match-candidates/{id}/decision` (`reviewer`, `admin`). A mid-confidence pair
  creates **no business at all** until a human decides: `merge` links the record and
  closes its sibling candidates, `keep_apart` on the last one creates a business of its
  own. Both write an audit entry.
- **The `resolution` job.** A discovery run that finishes `done` queues its own
  resolution run, keyed off the discovery run so a retry never leaves two behind.
  `POST /jobs/{id}/resolve` (`admin`, `tech_admin`) re-runs one by hand. The run reports
  `{processed, linked_existing, created, needs_review, invalid}`, is safe to repeat, and
  produces one business for two records of the same new business seen in one run.
- **Business endpoints.** `GET /businesses` filtered by `industry, city, state,
  has_website, website_kind, business_status, q` with cursor pagination, and
  `GET /businesses/{id}` showing every field value with its provenance and every record
  behind it.
- **Demo data, so none of this needs a Google key.** `make load-demo-data` stores 40
  fictional Austin businesses (555 numbers, `.invalid` domains) laid out to hit every
  path: exact duplicates that auto-merge, near-duplicates and chain locations that go to
  review, and same-name businesses that stay apart. `austin_plumbers.expected.json`
  records what they must resolve to, and an integration test asserts it. The
  `demo_fixture` source is registered **only** under `local` or `development`.
- **Retention now reaches derived data.** `purge-expired` nulls expired
  `business_field_values` alongside the raw payloads, keeps the provenance rows, and
  recomputes each affected business. A business whose name has entirely expired reads
  `[expired] <place_id>`; re-discovery brings it back.

### Fixed

- **A merged business no longer mixes half of one record with half of another.**
  Survivorship picked every field on its own, which let a Facebook page from one record
  be shown beside a domain from another. `website`/`domain`/`website_kind` and the ten
  address fields now each survive as one group taken whole from a single record — the web
  group preferring a real site over a builder subdomain over a social page, the address
  group preferring one that reaches a street.
- **Swagger has an Authorize button.** The current-user dependency now declares an
  `HTTPBearer` scheme, so `openapi.json` carries it and protected routes reference it.
  `auto_error=False` keeps 401s in the project's own error envelope.
- **A forgotten password can be reset.** `make reset-password EMAIL=...` prompts twice
  without echoing (or reads `NEW_PASSWORD` for a scripted run), requires 12 characters,
  exits non-zero on an unknown email, and writes a `user.password_reset` audit entry.
  `users.token_version` is raised by the reset and carried in every token, so **every**
  access and refresh token that user already held stops working at once. `seed-admin` now
  warns that a generated password is shown only once.

### Changed

- Migration `0003_businesses` adds the three tables, `discovered_records.resolution_status
  / resolution_error / resolved_at` and the foreign key to `businesses` that v0.2.0 left
  open, `job_runs.params` and `users.token_version`.
- `Candidate` gains `primary_type` and `address_components`, so normalization can use
  structured address parts without learning which provider it is reading.
- `domain` falls back to the whole host when the bundled public-suffix snapshot does not
  know the suffix, instead of returning nothing. It can only under-merge, and it is what
  the spec's own rule says `domain` is.
- New deps: `phonenumbers`, `rapidfuzz`, `jellyfish`, `tldextract` — the last configured
  offline, with a test that fails if it ever reaches for the suffix list.

## [v0.2.0] - 2026-09-19

### Added

- **Source adapter contract.** `SourceAdapter` (`discover`, `fetch`, `validate`,
  `normalize`, `emit_events`, `get_rate_limit`, `get_source_metadata`) with a registry
  keyed by `sources.name`, and a typed error hierarchy that says whether retrying could
  help — `TransientError` and `RateLimitedError` can, `AuthError`, `SchemaError` and
  `QuotaExceededError` cannot, so those fail a run on its first attempt.
- **Shared outbound HTTP client** (`app/core/http.py`): 5 s connect / 20 s read timeouts,
  three attempts with exponential backoff and jitter, `Retry-After` honoured up to 60 s,
  one retry on an unparseable 2xx body, secret redaction, and a metering hook that
  records every attempt whether it succeeded or not.
- **Rate limiting and a cost guard** (`app/core/ratelimit.py`): a Redis token bucket per
  source plus a per-UTC-day call cap, both shared by every api and worker process.
- **Google Places adapter.** Text Search (New) only, with a configurable field mask,
  pagination to a per-job cap and both geo shapes (`city` + `state`, or a circular
  location bias). No Place Details call: Text Search already returns the whole mask.
- **Discovery storage.** `discovered_records` (unique per source and source record, with
  `source_url`, `payload_hash`, first/last discovery and `content_expires_at`),
  `record_sightings` (which run saw what, and where it ranked) and `api_calls` (one row
  per attempt, for cost tracking). Re-discovering a place updates it and adds a sighting
  rather than creating a second row.
- **The `discovery` job.** `POST /search-jobs/{id}/run` now runs real discovery, reports
  `{fetched, stored_new, updated, invalid, api_calls}` in the new `job_runs.result_summary`
  and stops at the next record when cancelled, keeping everything already stored.
- **Endpoints.** `GET /sources`, `PATCH /sources/{id}` (admin and tech_admin),
  `GET /search-jobs/{id}/runs`, `GET /jobs/{id}/records` and `GET /discovered-records/{id}`
  (admin, reviewer and tech_admin). Creating or updating a search job that names an
  unknown or disabled source is rejected with a 422.
- **Retention.** Stored Places content expires after `PLACES_CONTENT_TTL_DAYS`;
  `make purge-expired` nulls the payload and keeps the place ID, which the Maps Platform
  terms allow to be cached indefinitely.
- **Commands.** `make sync-sources` (now part of `make migrate`), `make purge-expired`
  and `make places-smoke` — the last being the only thing in the repository that calls a
  live API, and only when a human runs it.
- **Tests.** Recorded Places fixtures and 144 new tests covering the failure table, the
  token bucket and cap, pagination and caps, provenance, re-discovery, cancellation,
  purging and RBAC. An autouse respx router now fails any test that would really reach
  the network.

### Changed

- Migration `0002_discovery` adds the three tables and `job_runs.result_summary`.
- The default kind for a run started over the API is now `discovery`; the demo handler
  from v0.1.0 remains only as the job framework's reference handler in tests.
- The Places API key is scrubbed from log output alongside the other secrets.

### Fixed

- `raw_payload` is declared `none_as_null`, and `purge-expired` writes SQL `NULL`.
  SQLAlchemy stores a Python `None` in a JSON column as the JSON scalar `null`, which
  reads back as `None` but is not SQL `NULL`, so a purged record would otherwise have
  been re-reported as expired on every subsequent run.

## [v0.1.0] - 2026-09-19

### Added

- **Stack.** Docker Compose with `postgres:16`, `redis:7`, `api`, `worker` and `web`,
  each with a health check; multi-stage Dockerfiles for the backend and the frontend.
- **Backend skeleton.** FastAPI + Pydantic v2 + SQLAlchemy 2 modular monolith managed with
  `uv`, laid out as `app/core` plus `app/modules/*` and `app/workers`.
- **Configuration.** Pydantic Settings read from environment variables only, documented in
  `.env.example`.
- **Database.** Alembic migration `0001_foundation` creating `users`, `sources`,
  `search_jobs`, `job_runs` and `audit_logs`, with native enums and the `citext` extension.
  `audit_logs` is append-only, enforced by a trigger rather than convention alone.
- **Observability.** Structured JSON logging with request-ID correlation, a request-ID
  middleware, a single error envelope
  (`{"error": {code, message, request_id, details}}`) and exception handlers for
  application, validation and unexpected errors.
- **Auth and RBAC.** Argon2 password hashing, 15-minute JWT access tokens, a refresh token
  in an httpOnly cookie, `POST /auth/login|refresh|logout`, `GET /auth/me` and a
  `require_role(...)` dependency covering `admin`, `sales_rep`, `reviewer`, `tech_admin`
  and `crm_manager`.
- **Users.** `POST /users`, `GET /users` and `PATCH /users/{id}` for admins, plus
  `make seed-admin` to create or promote the bootstrap admin.
- **Search jobs.** CRUD under `/search-jobs` with geo validation (`city` + `state`, or
  `lat` + `lng` + `radius_m`) and opaque cursor pagination on every list route.
- **Job framework.** `POST /search-jobs/{id}/run` enqueues an RQ job (deduplicated by
  `Idempotency-Key`), `GET /jobs/{id}/status` and `POST /jobs/{id}/cancel`. Runs move
  `queued → running → done | failed | cancelled`, carry progress counts, retry up to three
  attempts with exponential backoff and stop at the next checkpoint when cancelled.
- **Audit log.** Every auth event and every job-run status change appends a row recording
  actor, action, entity and the before/after snapshot.
- **Frontend.** Next.js (App Router) + TypeScript + Tailwind placeholder: a login form that
  keeps the access token in memory only and shows `/auth/me` alongside `/health`.
- **Tooling.** `make up|down|migrate|seed-admin|test|lint|check` and a GitHub Actions
  workflow running `make check` on `spec/**` pushes and pull requests.
