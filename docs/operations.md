# Operations runbook — production mode on one machine

This is how to run Lead Discovery Radar for real on a single machine (spec v0.8.0). Nothing
here is reachable from the network: the API and the web app listen on `127.0.0.1` only, and
PostgreSQL and Redis are not published at all. Moving to a server with a domain and TLS is a
separate, later spec.

## What runs

| Service | Where | Notes |
|---|---|---|
| `web` | http://127.0.0.1:3000 | Next.js, built with `NEXT_PUBLIC_API_BASE_URL` from `.env.prod` |
| `api` | http://127.0.0.1:8000 | FastAPI; `/docs` still works but nothing needs it any more |
| `worker` | (internal) | RQ worker + the scheduler thread (see below) |
| `postgres`, `redis` | (internal) | data in the `radar-prod_postgres-data-prod` / `radar-prod_redis-data-prod` volumes |

Production mode is the same images as development, started with an overlay:
`docker compose -p radar-prod --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml`.
Every `make` target takes `PROD=1` to point at it (`make migrate PROD=1`, `make backup PROD=1`);
`make prod-up`, `make prod-down`, `make prod-logs` and `make prod-ps` are the shortcuts.

## First start

1. `cp .env.prod.example .env.prod` and fill it in. Generate `JWT_SECRET` with
   `openssl rand -hex 32` and `POSTGRES_PASSWORD` with `openssl rand -hex 24` (put the same
   password in `DATABASE_URL`). Set `ADMIN_EMAIL` to the real administrator's address.
   **Never** copy the development `.env`: the startup checks refuse a short `JWT_SECRET`,
   `DEBUG=true`, `CRM_DESTINATION=fake`, `AI_PROVIDER=fake`, a `DEMO_USERS_PASSWORD`, an
   admin address under `example.com` and a `*` in `CORS_ORIGINS`, and list every problem at
   once in the container log.
2. `make prod-up` — builds the images and waits until every service is healthy. If the api
   or the worker keeps restarting, `make prod-logs` shows the refused settings.
3. `make migrate PROD=1` — applies the migrations and registers the sources.
4. `make seed-admin PROD=1` — creates the administrator from `ADMIN_EMAIL` / `ADMIN_PASSWORD`.
   With `ADMIN_PASSWORD` empty a strong password is generated and printed **once**, and that
   admin must change it on first sign-in.
5. Open http://127.0.0.1:3000, sign in, and use **Users** to create everyone else (each gets a
   temporary password they must replace). If `admin@example.com` ever existed in this
   database, the Users page shows a warning with a one-click *Deactivate*.
6. `make backup PROD=1` then `make backup-verify PROD=1` (below), and open **Health** to see
   that nothing is red.

Demo data, demo users and the fake CRM/AI providers do not exist in production: the
commands refuse (`APP_ENV is 'production' … refused`) and the settings are rejected at start.

## Where data lives

- The database: the `radar-prod_postgres-data-prod` volume. `make down PROD=1 ARGS=-v` deletes
  it; do not run that unless you mean it.
- Redis (queues, rate-limit counters, the scheduler's last-run times): `radar-prod_redis-data-prod`.
  Losing it loses nothing that matters — counters restart, the scheduler's clocks start again.
- Backups: `./backups/` on the host (bind-mounted into the api and worker containers).
- Logs: `./logs/api.log` and `./logs/worker.log`, JSON, one line per event, rotated daily at
  midnight UTC, `LOG_KEEP_DAYS` (14) old files kept. Secrets are redacted before they are
  written. `make prod-logs` follows the same lines on stdout.
- `.env.prod`: the only copy of the secrets. **A backup does not include it.** Copy it somewhere
  safe (a password manager entry is fine) whenever it changes; a restored database without it
  cannot sign anyone in or call any API.

## Backups

- `make backup PROD=1` — `pg_dump -Fc` into `./backups/radar-YYYYMMDD-HHMMSS.dump`, then keeps
  only the newest `BACKUP_KEEP` (14). Recorded as a `backup` job run.
- The scheduler does the same every day at `BACKUP_AT` (02:00 in `TIMEZONE`).
- Copy the newest dump **and** `.env.prod` off the machine regularly; the retention window is
  two weeks, not forever.

### Verifying a backup

`make backup-verify PROD=1` restores the newest dump into a throw-away database on the same
server, checks that `alembic_version` matches this code and that the core tables came back
(row counts are printed), and drops it. The scheduler runs it every Sunday at 04:00; a failure
raises a **critical** alert (`backup_verify_failed`) that stays until acknowledged. Verify a
backup by hand after every upgrade and before relying on a copy you moved elsewhere.

### Restoring

```
make restore FILE=backups/radar-20260921-020000.dump PROD=1
```

It prints what it is about to replace and asks you to type `RESTORE <filename>`. It then
stops the api and the worker, runs `pg_restore --clean --if-exists` into the live database,
runs the migrations and starts the api and the worker again. Nothing else on the machine is
touched. Sessions survive (the JWT secret did not change) but anything done after the dump is
gone, including users created since; tell the team before you do it.

## The scheduler

One scheduler thread runs inside the worker (`app/workers/scheduler.py`, `croniter`, times in
`TIMEZONE`). A Redis lock guarantees a single instance even if two workers are started.
Every execution is a job run of kind `scheduled:<name>`, visible on the Health page under
*Queue and scheduler* and in the audit log.

| Job | When | What |
|---|---|---|
| `crm-sync` | every minute | sends CRM leads whose undo window has closed |
| `watchdog` | every 5 minutes | fails runs stuck in `running` > `WATCHDOG_STALE_MINUTES` ("worker lost"), re-evaluates the alert rules |
| `purge-expired` | daily `PURGE_AT` (03:00) | drops expired Places content, audit page text, raw AI output |
| `backup` | daily `BACKUP_AT` (02:00) | `pg_dump` as above |
| `backup-verify` | `BACKUP_VERIFY_CRON` (Sunday 04:00) | restore-and-check as above |

A job's clock starts when the scheduler first sees it, so a restart never replays missed
nights; a job whose previous run is still queued or running is not queued again.

## Health page and alerts

**Health** (admins and tech admins) shows: job success rate by kind (24 h / 7 d), external API
error rate by source (24 h), median and p95 processing time per kind, queue length and the
scheduler, AI calls / reuse rate / spend against today's budget, data quality (invalid records,
businesses missing city / phone / website), duplicates (7 d), CRM counts, source freshness,
audit outcomes (7 d), and the last backup and verify.

Alerts appear in a banner on every page for admins and tech admins and are also `WARNING`
lines in the logs. Thresholds are settings (`ALERT_*`):

| Rule | Fires when | Clears |
|---|---|---|
| `job_success_rate` | any kind under 80 % in 24 h | on its own when the rate recovers |
| `source_error_rate` | any source over 20 % errors in 24 h | on its own |
| `crm_held` | a CRM lead is held | when no lead is held (fix it on the CRM page) |
| `ai_budget` | spend (or calls, without prices) ≥ 80 % of today's budget | at UTC midnight |
| `backup_age` | no backup in 36 h (once the install is older than that) | after the next backup |
| `queue_length` | more than 500 jobs waiting (critical) | when the worker catches up |
| `stale_job` | the watchdog failed a stuck run | when acknowledged |
| `backup_verify_failed` | a verify failed (critical) | when acknowledged |

*Acknowledge* hides an alert. A condition alert that is still true stays hidden until it
clears; if it comes back later it is a new alert.

## Everyday tasks

- **Add a user:** Users → email, role, temporary password (Generate) → Create. Hand the
  password over in person; they must change it on first sign-in.
- **Unlock / reset:** ten failed sign-ins lock an account for 15 minutes (six in 15 minutes
  from one address get a 429 first). The Users page shows a red *locked until …* chip for
  the account lock and an amber *Temporarily blocked (until HH:MM)* chip while any address
  is still rate limited; *Unlock* clears both — the lock and every failed-attempt counter
  for that email — and is audited (`user.unlocked`, with how many addresses were cleared).
  *Reset password* sets a new temporary one.
- **Run a search:** Searches → industry, city + state (or a point and radius), max results →
  read the cost estimate (Places pages, today's remaining caps, expected PageSpeed and AI
  calls) → *Save and run*. Follow the four stages on the search's page; *Open the review
  queue* filters the queue to that city. Re-running within seven days warns: it costs API
  calls and usually finds the same businesses.
- **Held CRM leads:** the banner says how many. CRM page → the held row shows the error
  (token, base, field) → fix the cause → *Retry*.
- **Rotate `JWT_SECRET`:** put a new value in `.env.prod`, `make prod-up`. Everyone is signed
  out and signs in again; nothing else changes.
- **Rotate an API key** (Places, PageSpeed, OpenAI, Airtable): edit `.env.prod`, `make prod-up`
  (the api and the worker restart with the new value), then `make crm-check PROD=1` for
  Airtable or a run of a small search for Places.
- **Dependency advisories:** `make audit` (needs the network); CI runs it on every push.

## Upgrading to a new version

1. `make backup PROD=1` and copy the dump off the machine.
2. `git pull` (or check out the tag).
3. `make prod-up` — rebuilds the images and restarts the services; the startup checks run
   again with the current `.env.prod` (new settings have defaults, but read the release
   notes for any that need a value).
4. `make migrate PROD=1`.
5. Open **Health**: no red alerts, backup time shown; `make backup-verify PROD=1`.

If something is wrong, `make restore FILE=… PROD=1` puts the database back; check out the
previous tag and `make prod-up` again.
