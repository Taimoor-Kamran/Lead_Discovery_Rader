# Changelog

All notable changes, one section per merged spec. Newest first.
Format: `## [vX.Y.Z] - YYYY-MM-DD` followed by Added / Changed / Fixed.

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
