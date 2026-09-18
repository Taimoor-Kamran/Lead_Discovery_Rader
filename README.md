# Lead Discovery Radar — MVP build kit

Spec-driven build of the Lead Discovery Radar MVP using Claude Code (CLI).

## One-time setup

```bash
# 1. Create the repo and drop this kit in
git init lead-discovery-radar && cd lead-discovery-radar
# copy CLAUDE.md, README.md, specs/, scripts/, .claude/ here
chmod +x scripts/*.sh .claude/hooks/*.sh
echo ".claude/state/" >> .gitignore
echo ".env" >> .gitignore
git add . && git commit -m "chore: add spec-driven build kit"
git branch -M main

# 2. Tools you need locally: git, docker + compose, jq, make, Claude Code CLI
```

## The loop (one spec at a time, in order)

```bash
scripts/spec-start.sh v0.1.0     # branch spec/v0.1.0-foundation from main, set active spec
scripts/spec-run.sh              # Claude CLI implements the spec; Stop hook keeps it going until done
# --- you: review the diff, run the app, test it by hand ---
scripts/spec-run.sh "fix: the job list is not paginated"   # optional follow-up passes
scripts/spec-finish.sh           # gate check → merge --no-ff into main → tag v0.1.0
```

Then `scripts/spec-start.sh v0.2.0` and repeat.

Rules: one active spec at a time, specs merge in version order, `main` only ever receives
`--no-ff` merges from `spec/*` branches, and every merge gets a tag.

## Spec order and estimate

| Version | Branch | Scope | Est. days |
|---|---|---|---|
| v0.1.0 | spec/v0.1.0-foundation | Repo, Docker, DB, auth/RBAC, job framework | 2–3 |
| v0.2.0 | spec/v0.2.0-discovery | Adapter contract + Google Places adapter | 3–4 |
| v0.3.0 | spec/v0.3.0-normalize-dedupe | Normalization + entity resolution | 3–5 |
| v0.4.0 | spec/v0.4.0-website-audit | Safe fetch + deterministic audit + PSI | 3–4 |
| v0.5.0 | spec/v0.5.0-ai-scoring | Claude classification + 4-part scoring | 3–4 |
| v0.6.0 | spec/v0.6.0-review-queue | Next.js UI + human review gate | 4–5 |
| v0.7.0 | spec/v0.7.0-crm-export | Airtable export + sync log | 2–3 |
| v0.8.0 | spec/v0.8.0-hardening | Security, monitoring, retention, E2E → tag v1.0.0 | 4–5 |

Total ≈ 27–37 working days (6–8 weeks) of build; 8–10 weeks to a signed-off v1.0.0.

## Running your first real search

Everything below needs a Google key, so it is a human job. Nothing in the test suite
touches a live API — the tests run entirely on recorded responses.

**1. Get a key, and put a ceiling on it.**

- In Google Cloud, pick (or create) a project and **enable billing** on it.
- Enable **Places API (New)**.
- Create an API key and **restrict it to Places API (New)** only.
- In Billing → Budgets & alerts, set a **budget alert** (e.g. $20/month) *before* the
  first live call. Discovery is metered, capped and rate limited, but a budget alert is
  the only thing that catches a mistake nobody predicted.
- Check the current [Places pricing](https://developers.google.com/maps/documentation/places/web-service/usage-and-billing):
  the default `PLACES_FIELD_MASK` asks for phone, website and address components, which
  fall into the **Enterprise** SKU. Trim the mask if you want the cheaper Pro tier.
- Check the current [Maps Platform terms](https://cloud.google.com/maps-platform/terms)
  for how long Places content may be stored, and set `PLACES_CONTENT_TTL_DAYS` to match
  (default 30). Place IDs may be kept indefinitely.

**2. Configure and start.**

```bash
cp .env.example .env         # then set GOOGLE_PLACES_API_KEY, JWT_SECRET and ADMIN_EMAIL
make up
make migrate                 # applies migrations, then registers the adapters as sources
make seed-admin
```

`.env` is never committed, and the key never reaches a log line, an `api_calls` row, a job
error or a stored payload — there is a test that proves it with a sentinel key.

**3. Smoke-test the key** (one live call, prints a table, stores nothing):

```bash
make places-smoke
make places-smoke ARGS="--industry dentist --city Portland --state OR --max 5"
```

**4. Run a real search** at <http://localhost:8000/docs>:

1. `POST /auth/login` with the admin credentials, then click **Authorize** and paste the
   access token.
2. `GET /sources` — copy the `id` of `google_places`.
3. `POST /search-jobs` with an industry, a geo (`city` + `state`, or `lat`/`lng`/`radius_m`)
   and that source id. A job naming an unknown or disabled source is rejected with a 422.
4. `POST /search-jobs/{id}/run` — note the returned run id.
5. `GET /jobs/{run_id}/status` until it reads `done`; `result_summary` reports
   `{fetched, stored_new, updated, invalid, api_calls}`.
6. `GET /jobs/{run_id}/records` for what it found, and
   `GET /discovered-records/{id}` for one record in full, payload and all.
7. Run the job again: the record count stays the same, `last_discovered_at` moves and a
   second sighting is added. Re-discovery never duplicates a place.
8. Compare `api_calls` with the request count in the Google Cloud console.

**Cost and retention controls**

| Setting | What it does |
|---|---|
| `PLACES_MAX_RESULTS_PER_JOB` | Results per job. Text Search returns at most 60 (3 pages of 20) |
| `PLACES_DAILY_CALL_CAP` | Calls per source per UTC day. Exceeding it fails the run *before* the call |
| `PLACES_RPS` | Outbound requests per second, shared across every api and worker process |
| `PLACES_CONTENT_TTL_DAYS` | How long a payload is kept. `make purge-expired` nulls it and keeps the place ID |

Known limit: one query returns at most 60 results, so a dense city is not exhaustively
covered. Splitting an area into tiles is out of scope for v0.2.0.

## Testing dedupe with demo data

No Google key needed. `backend/app/demo/austin_plumbers.json` holds 40 **fictional**
businesses — 555 phone numbers, `.invalid` domains, nothing taken from a real response —
laid out to exercise every path through entity resolution at once.

```bash
make down && make up && make migrate && make seed-admin
make load-demo-data
```

That stores the fixture as a discovery run and queues the resolution run that dedupes it.
The command prints both run ids; watch the second with `GET /api/v1/jobs/{id}/status`
until it reads `done`.

What you should then see:

| Case | In the fixture | What resolution does |
|---|---|---|
| Exact duplicates | 5 businesses, each under two place IDs | **auto-merged** — one business, two records |
| Near-duplicates | `ABC Plumbing LLC` next door to `ABC Plumbing & HVAC` | **needs review** — no business is created until a human decides |
| Chain locations | 3 addresses sharing one domain | **needs review** — a shared domain alone never merges |
| Same name, different business | `Reliable Plumbing` in Austin and in Dallas | **kept apart** — they never even become candidates |

- `GET /api/v1/businesses?city=Austin` → 29 businesses in total.
- `GET /api/v1/businesses/{id}` → every value with the source, the record and the time it
  was observed. Open a merged one and you will see both records under `records`.
- `GET /api/v1/match-candidates?status=pending` → 6 pairs waiting on a human.
- `POST /api/v1/match-candidates/{id}/decision` with `{"decision": "merge"}` links the
  record to that business; `{"decision": "keep_apart"}` on the last pending candidate
  creates a business of its own. Either way an audit entry is written, and a `sales_rep`
  gets a 403.

The expected numbers live in `austin_plumbers.expected.json` and are asserted by
`backend/tests/integration/test_demo_dataset.py`, so changing a weight or a threshold
fails the suite rather than quietly producing a different answer.

`make load-demo-data` is idempotent — run it as often as you like. The `demo_fixture`
source is registered **only** when `ENVIRONMENT` (or `APP_ENV`) is `local` or
`development`, so fictional businesses cannot reach staging or production.

## Resetting a password

```bash
make reset-password EMAIL=you@example.com
```

It asks for the new password twice and never echoes it, so nothing lands in your shell
history. The password must be at least 12 characters, and an unknown email exits non-zero
without changing anything.

A reset raises that user's `token_version`, which **immediately invalidates every access
and refresh token they already hold** — any other session is logged out at its next
request. Other users are untouched. The reset is recorded in the audit log as
`user.password_reset` with no actor, because a terminal has none.

For a scripted reset, set `NEW_PASSWORD` in the environment instead of typing it:

```bash
NEW_PASSWORD='a-long-enough-passphrase' make reset-password EMAIL=you@example.com
```

`make seed-admin` prints a generated password exactly once and says so. If you lose it,
use the command above.

## Versioning

- `v0.x.0` = one spec merged. `v0.x.y` = a fix spec on top of it (e.g. `specs/v0.3.1.md`,
  branch `spec/v0.3.1-dedupe-threshold-fix`).
- `v1.0.0` = MVP release, tagged by hand after v0.8.0 is merged and the release checklist
  in `specs/v0.8.0.md` is signed off.

## Writing new specs

Specs are written one at a time: after a spec is merged, write the next one against what now
exists in main.

Copy `specs/_TEMPLATE.md` to `specs/vX.Y.Z.md`. Keep one spec to roughly 2–5 days of work;
if it's bigger, split it. Put anything only a human can do under **Human prerequisites** as plain
bullets (not checkboxes), so it never blocks the Stop hook.
