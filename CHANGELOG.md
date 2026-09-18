# Changelog

All notable changes, one section per merged spec. Newest first.
Format: `## [vX.Y.Z] - YYYY-MM-DD` followed by Added / Changed / Fixed.

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
