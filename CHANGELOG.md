# Changelog

All notable changes, one section per merged spec. Newest first.
Format: `## [vX.Y.Z] - YYYY-MM-DD` followed by Added / Changed / Fixed.

## [v0.9.0] - 2026-09-21

### Added

- **Design tokens** (`frontend/src/app/globals.css`): colour, type, space, radius,
  elevation and motion as CSS variables. `tailwind.config.ts` **replaces** Tailwind's
  palette, type scale, radii and shadows with them, so `bg-slate-50`, `text-2xl` and
  `rounded-xl` no longer exist and a component cannot reach past the system.
- **Self-hosted typefaces**: Public Sans (UI) and IBM Plex Mono (evidence, URLs, scores
  and IDs) as latin subsets under `frontend/public/fonts`, `font-display: swap`. No font
  CDN, so the Content-Security-Policy keeps `font-src 'self'`.
- **Component layer** (`frontend/src/components/ui/`): Button, Input, Select, Checkbox,
  Textarea, Table (sortable headers, tabular numerals, its own scroll container), Card,
  Badge, SeverityDot, Chip, Dialog (focus trap, Esc, focus restored), Toast, Tabs
  (arrow keys), Disclosure, Tooltip, Skeleton, EmptyState, PageHeader, Pagination — one
  implementation each, each with a test.
- **Accessibility**: a skip-to-content link, one visible focus ring everywhere, real
  landmarks and one `h1` per page, and `src/test/axe.test.tsx` running axe-core over
  login, the queue, review detail, leads, lead detail, CRM, searches and health with
  zero serious or critical violations.
- **Print stylesheet for `/leads/[opportunityId]`**: one A4 page, no navigation, the
  phone and website as a letterhead, the evidence underneath.
- **Guard tests**: `src/test/tokens.test.ts` (no raw hex, off-scale size, off-system
  radius, stale palette class, all-caps label or `·`-joined string in any component, and
  `globals.css` agrees with `src/lib/tokens.ts`) and `src/lib/contrast.test.ts` (all 26
  token pairs the UI sets text on clear WCAG AA).
- `docs/design.md` — tokens, type scale, component inventory, the two-column review
  rationale, the opportunity/lead vocabulary rule and what was rejected and why.
  `docs/design/before` and `docs/design/after` hold a screenshot of every screen at
  1280 px; `make screenshots OUT=…` regenerates them.

### Changed

- **Review detail is a two-column argument** instead of three equal panels: who the
  business is, what the audit found as one severity-ranked list (not six stacked cards)
  and the AI summary on the left; the decision column, sticky, on the right.
- Every screen was refitted: denser tables with the score as the rightmost column in
  tabular figures, skeletons instead of "Loading…", empty states that name the next
  action, and errors that state a cause and a fix ("Couldn't reach the API. Check that
  the api container is running (`make ps`)."). That fix names the stack the build belongs
  to — `make ps` in a development build, `make prod-ps` in a production one — from the new
  `NEXT_PUBLIC_ENVIRONMENT` build arg, which compose fills from `ENVIRONMENT`.
- **Codes are read, not shown.** Industry, website kind and business status join services,
  findings and severities in `lib/labels.ts`: the facts panel says *Plumbing*, *Own
  website* and *Open* where it used to print `plumbing`, `own_site` and `operational`,
  with the raw code kept in the element's tooltip. The field-provenance table still shows
  stored values verbatim, because that table is about the values themselves.
- Sentence case throughout: no tracked-out all-caps labels, no eyebrows above headings,
  and no `·`-joined meta strings — the health metrics read as sentences now.
- `prefers-reduced-motion: reduce` switches off every transition and animation.
- `components/Toast.tsx` moved to `components/ui/Toast.tsx`; its API is unchanged.

### Fixed

Seven bugs from the first real production run and the `make e2e` smoke that followed,
all traced from `logs/worker.log`.

- **The scheduler thread no longer shares a database connection with job execution.**
  The worker runs the scheduler in a daemon thread *and* forks a work horse for every
  job, and both used the one engine — so a connection the scheduler had used was copied
  into a child that then wrote on the same socket. psycopg names its prepared statements
  `_pg3_N` per connection and counts them client-side, so the two collided
  (`DuplicatePreparedStatement`, `InvalidSqlStatementName`): "scheduler tick failed"
  every five minutes, and a real discovery run killed after four places. The scheduler
  now has its own `NullPool` engine, a forked child disposes the connections it
  inherited (`os.register_at_fork`, `close=False`), and prepared statements are off on
  every engine. *Verified in production:* the last `scheduler tick failed` and the last
  `DuplicatePreparedStatement` are both 2026-09-21T21:24:02Z, 43 minutes before the
  fixed worker started (22:07:59Z); none since, where before they landed on every
  five-minute tick. The scheduler is live across that gap — `scheduled:crm-sync` 48 runs
  done, `scheduled:watchdog` 36.
- **No job run can be left `running` or `queued` with no error.** `execute_job_run`
  wrote every outcome through the session the handler had just been using, and the two
  transitions bracketing the handler sat outside the try — so a broken connection either
  left a finished run `running` for ever or rolled it back out of `running` to `queued`
  with nothing on the queue. `running` is now committed before any work starts, a
  handler failure is recorded in its own session first (so an alert it wrote survives),
  and anything that escapes an attempt is written to the run on a brand-new session.
  `queued → failed` joins the state machine for the runs that died before they started.
- **The watchdog finishes stuck runs of every kind, including `scheduled:*`.** It only
  looked at `running`; a run abandoned in `queued` was invisible to it and, since the
  scheduler skips a job whose previous run has not finished, blocked that job for good —
  one `scheduled:crm-sync` run stopped every later CRM sync for a day. Those are now
  failed with "queue lost", checked against RQ rather than guessed from the clock.
  *Verified in production:* that same run, `910e22fd-741f-45f9-bf4a-92155576e2e5`, had
  been `queued` for 23 h 51 m; the fixed worker's watchdog failed it at
  2026-09-21T22:08:00Z with "queue lost" (`attempts=0` — it never started), one second
  after the worker came up, and `crm-sync` has run every minute since.
- **A discovery run broken partway through a page is retried instead of truncated.** The
  run that stored 4 of 20 had the whole page in hand; it lost the other 16 to the crash
  and was never retried. With the failure recorded properly the attempt retries, and
  `store_raw` being an upsert means the retry ends with everything stored.
- **A lost race on an opportunity no longer costs a business its classification.**
  `upsert_opportunities` inserted everything it had not found in one flush, so a
  `pending` row committed by anybody else in between violated
  `uq_opportunities_pending_business_service`, aborted the transaction and threw the
  business away ("classifying one business failed"). Each new row now goes in inside its
  own savepoint; a conflict on that index means somebody opened the row first, so it is
  taken over and updated. Any other integrity error is still raised. (The "anybody else"
  turned out to be the sixth bug below, not the connection bug above.)
- **A job run now has exactly one executor.** `reset-demo-data` and `load-demo-data`
  queued their runs to RQ *and* executed them in the CLI process, so the worker claimed
  the same rows. The second claimant raised `A job run cannot go from 'running' to
  'running'` and then wrote `failed`, with an empty `result_summary`, over work the first
  had already committed — which broke `make e2e` at its first step, and which is what was
  really creating the duplicate opportunities above. `enqueue_run` now takes
  `dispatch=False` for a caller that will execute the run itself, and `follow_up` carries
  that decision down the discovery → resolution → audit → classification chain. Separately,
  an attempt that finds a run already `running` backs off instead of raising: RQ
  redelivers a job when a worker dies, and judging an abandoned run is the watchdog's job.
  `running → running` remains forbidden in the state machine — it caught a real
  double-execution, and permitting it would only hide the next one.
- **`make load-demo-data` exits non-zero when one of its runs did not finish.** Backing
  off on an already-`running` run is the one non-terminal exit from `execute_job_run`,
  and the command printed all three of its run statuses without reading any of them — so
  an unfinished run still ended with "Now try GET /api/v1/opportunities" and a green
  shell, over opportunities that were never classified. It now names what did not finish
  and returns 1, as `reset-demo-data` always has.

## [v0.8.0] - 2026-09-20

### Added

- **Production mode on one machine**: `docker-compose.prod.yml` + `.env.prod.example`,
  `make prod-up/prod-down/prod-logs/prod-ps` (and `PROD=1` on every other target). Ports on
  127.0.0.1 only, PostgreSQL and Redis not published, `restart: unless-stopped`, memory
  limits, separate `*-prod` volumes, `./backups` and `./logs` bind mounts.
- **Startup checks** (`app/core/startup.py`): production refuses a short `JWT_SECRET`,
  `DEBUG=true`, demo fixtures, `CRM_DESTINATION=fake`, `AI_PROVIDER=fake`, an admin under
  `example.com`, a `DEMO_USERS_PASSWORD` and a `*` CORS origin — all listed at once, one test
  per rule. The demo commands refuse uniformly outside development. The refresh cookie is
  always `Secure` in production (127.0.0.1 is a secure context).
- **Backups**: `make backup` (`pg_dump -Fc`, newest `BACKUP_KEEP` kept), `make restore FILE=…`
  (typed confirmation, stops api + worker, migrates after), `make backup-verify` (restores
  the newest dump into a throw-away database, checks `alembic_version` and the core tables,
  drops it). Both operator commands are `job_runs`. PostgreSQL 16 client tools in the image.
- **Scheduler** (`app/workers/scheduler.py`, croniter loop in the worker, Redis lock for a
  single instance): `crm-sync` every minute, `watchdog` every 5 minutes (stuck runs → failed
  "worker lost" + alert), `purge-expired` daily, `backup` daily, `backup-verify` weekly. Every
  execution is a `scheduled:<name>` job run.
- **Monitoring**: `GET /admin/health` with every slide-45 metric; `alerts` table with rules
  (job success rate, source error rate, held CRM leads, AI budget, backup age, backup verify
  failed, stale job, queue length), thresholds in config, `POST /admin/alerts/{id}/acknowledge`;
  JSON logs also to `LOG_DIR/*.log` with daily rotation, 14 days kept.
- **Security**: login rate limit (5 failures per email+address in 15 min → 429) and account
  lockout (10 failures → 15 min), all audited; `must_change_password` for admin-created and
  admin-reset users with `POST /auth/change-password` and password rules (12+, not the email,
  not a common password); security headers on the API (`nosniff`, `DENY`, `no-referrer`,
  `Cache-Control: no-store` on authenticated answers, HSTS only over https) and the web app
  (the same plus a nonce-based Content-Security-Policy, documented in `frontend/src/lib/csp.ts`);
  strict CORS (exact origins, explicit methods/headers); SafeFetcher now **pins the
  connection** to the validated address (`Host` + SNI keep the name) so DNS cannot change
  between check and connect; `make audit` (`pip-audit` + `pnpm audit --prod`) in CI; a secrets
  hygiene test greps the repo for key-shaped strings.
- **Admin UI**: `/admin/users` (create with a generated temporary password, role, deactivate /
  reactivate, unlock, reset; placeholder-admin warning), `/searches` (industry dropdown, city +
  state or point + radius, max results, **cost estimate before running**, Run disabled when the
  daily cap would be exceeded, history with a 7-day re-run warning) and `/searches/{id}` (the
  four pipeline stages with counts and errors, link to the review queue by city),
  `/admin/health` with the alert banner on every page, `/profile` (change own password) with
  the forced-change redirect.
- **API for the pages**: `GET /search-jobs/industries`, `POST /search-jobs/estimate`,
  `GET /search-jobs/{id}/estimate`, `GET /search-jobs/{id}/pipeline`, `max_results` on search
  jobs, `last_run` on the list, `POST /users/{id}/unlock`, `POST /users/{id}/reset-password`.
- **Docs**: `docs/operations.md`, `docs/pilot.md`, `docs/release-checklist.md`; README section
  "Running for real on this machine".

### Changed

- `/crm` scheduled and held rows show the approved services before the first sync.
- `pytest-xdist`: one PostgreSQL container, one database per worker; `make test` runs
  `-n auto` (`TEST_ARGS=-n0` for serial). `make check` went from 9 min 59 s to 6 min 28 s
  on the dev machine (backend suite 535 s → 344 s).
- `make e2e` checks `CRM_DESTINATION=fake` first and explains what to do otherwise.
- Dependencies: lxml 6.1, pytest 9 (advisories), pytest-asyncio 1.x, croniter, types-croniter,
  pytest-xdist, pip-audit, httpx2 (removes the Starlette TestClient deprecation); ESLint
  9.39.5; pnpm overrides for sharp ≥ 0.35.4 and postcss ≥ 8.5.28.
- The CRM sync thread of v0.7.0 is replaced by the scheduler's `crm-sync` job; the old loop
  stays available for `SCHEDULER_ENABLED=false`.

### Fixed

- **Rate limit vs lockout on the Users page** (manual review): after the 5-attempt rate
  limit the row read *LOCKED —* and an admin could do nothing. `GET /users` (admins) now
  carries `rate_limited_until` / `rate_limited`, read from the Redis counters of every
  address that failed for that email; the page shows *Temporarily blocked (until HH:MM)*
  beside the 15-minute lock; *Unlock* clears the account lock **and** every rate-limit
  counter for the email, and the `user.unlocked` audit row records how many addresses it
  cleared.
- **`make e2e` no longer leaves a new `e2e-<timestamp>@example.com` user behind each run.**
  The smoke signs in as one fixed `e2e-user@example.com` (created on the first run,
  reactivated + reset on the next), and `make reset-demo-data` removes every
  `e2e-*@example.com` user: deleted when nothing references them, otherwise deactivated
  (the append-only audit log refuses the `SET NULL` for anyone who signed in, and a search
  job or decision is `RESTRICT`), both audited.
- **Undoing a do-not-contact scheduled the CRM flag removal at wall-clock time**, not at the
  moment the undo was decided at: the review suppression's lift ignored the caller's clock.
  It is threaded through now (`lifted_at` and the CRM follow-up share the undo's instant).
  Found because the test's fixed date was today: it passed until 14:05 UTC and failed after.

## [v0.7.0] - 2026-09-20

### Added

- **CRM export** (`app/modules/crm`, migration `0007`). Approved leads leave the system —
  and only approved leads: a business is sent when it has at least one `approved`
  opportunity and no active suppression, checked when the sync is scheduled **and again
  immediately before every call to an adapter** (tested over every destination).
- **One CRM record per business** listing its approved services (`Website redesign; Online
  booking`), highest score, the rules' reasons, the latest audit's findings in plain words,
  source, Radar link, dates, reviewer — and four **CRM-owned** fields (Assigned rep, Status,
  Follow-up date, Notes) written once on create and never overwritten.
- **Undo-safe delay**: each approval waits its own undo window (`CRM_SYNC_DELAY_MINUTES`,
  default = `REVIEW_UNDO_WINDOW_MINUTES`) before it is sent; an approval undone in time
  never leaves. Undoing after the sync removes the service from the record, or sets Status
  to `Withdrawn` while it still reads `New`.
- **Destinations**, swappable with `CRM_DESTINATION`: `csv` (default; `GET /crm/export.csv`
  with `scope=new|all`, UTF-8 BOM, RFC 4180, formula characters neutralised with a leading
  `'`), `airtable` (through `core/http.py`, metered under the `airtable` source row, token
  never logged; `crm_field_map.airtable.json` for column names; `make crm-check` and the
  optional `make crm-bootstrap-airtable`; guide in `docs/crm/airtable-setup.md`) and `fake`
  (in-database, development/ci only, for the demo and `make e2e`).
- **Dedupe**: the stored id first, then the CRM's own search by Radar Business ID, domain
  and phone — an existing record is linked, not duplicated. **Unchanged** payload → no call.
- **Retry**: 429/5xx/timeouts back off (60s, 120s, honouring `Retry-After`) for three
  attempts, then the lead is `held`; auth, config and rejected errors hold at once. Every
  attempt is a `crm_sync_attempts` row and an audit entry.
- **Suppression propagation**: a do-not-contact on a business already in the CRM sets its
  Do not contact field; lifting it clears the flag. A CRM record is never deleted.
- **Endpoints** `GET /crm/status`, `GET /crm/leads?status=`, `GET /crm/leads/{id}/attempts`,
  `POST /crm/leads/{id}/retry`, `POST /crm/businesses/{id}/sync-now`, `POST /crm/sync-all`,
  `GET /crm/export.csv`; `GET /leads` and `GET /leads/{id}` carry a `crm` block (and the
  detail a `crm_history`).
- **UI**: CRM status badges on My leads and the lead page (Scheduled with time, In CRM ✓
  linking to the record, Held ⚠ with Retry for CRM managers and admins), sync history on the
  lead page, and a `/crm` page (destination and health, counts, held and scheduled lists,
  Export CSV new/all, Sync all due) in the nav for CRM managers, admins and tech admins.
- **Worker**: a sync loop beside the RQ worker sends due leads every
  `CRM_SYNC_INTERVAL_SECONDS`.

### Fixed

- The "AI-generated — verify before use" label appears once per AI item (the AI summary
  box and the AI rationale line); the extra banner at the top of each opportunity card and
  the lead page header is gone.
- A classification reused from the cache is not counted as an escalation in the run summary
  or `/ai/usage`.

## [v0.6.0] - 2026-09-20

### Added

- **Lead detail** `GET /leads/{opportunity_id}` and the read-only page `/leads/[id]`:
  business facts, plain-language findings with evidence, the reason, the evidence and the
  approval. A sales rep gets a 403 on a lead not assigned to them (tested).
- `ReviewOpportunity` / `LeadRead` carry `rule_reason` and `ai_rationale` (the stored
  `reason` taken apart for display; nothing reworded).
- `make reset-demo-data` (development only): removes every decision, suppression and
  opportunity and classifies the demo again. `make e2e` runs it first.

### Changed

- Review UI after the manual pass: human labels for services, finding codes, audit
  statuses and sources (raw code in the tooltip); scores shown as 0–100 integers; queue
  chips read "Score 78" with confidence in the tooltip; queue checkboxes appear on hover or
  when weak signals are shown; rule reason and AI rationale on two labelled lines; one
  evidence item per finding with an "AI agrees" badge; US phones as `(512) 555-0102`; AI
  provenance behind a "Details" disclosure; an open-opportunities strip with jump links and
  sticky decision buttons on the detail page; PageSpeed as "Mobile score 55/100", "Load
  time (LCP) 3.6 s", "Layout shift (CLS) 0.11" with good / needs work / poor bands.

- **Human review** (`app/modules/review`, migration `0006`). Six decisions per opportunity —
  approve, reject, needs enrichment, duplicate, not a fit, do not contact — each with the
  fields the blueprint requires (reason codes for reject / not-a-fit, a note for
  needs-enrichment / do-not-contact / "other", a same-service `duplicate_of`, an active
  `sales_rep` for `assigned_to`). Only a `pending` or `needs_enrichment` row can be decided.
  Every request echoes the row's `lock_version`; a stale one is a **409**
  (`stale_lock_version`, "Another reviewer already decided this"). Every decision writes a
  `review_decisions` row and an `audit_logs` row in the same transaction.
- **Undo** (`POST /review-decisions/{id}/undo`) within `REVIEW_UNDO_WINDOW_MINUTES` (30)
  by the person who decided or any admin: the previous status is restored, the history row
  is marked `undone_at`, and a do-not-contact undo lifts the suppression it created and
  restores every row it closed.
- **Batch** (`POST /opportunities/review-batch`): reject and not-a-fit only, at most 50
  ids, a per-id result (`ok` / `conflict` / `not_allowed`). Approvals and do-not-contact
  are never batched.
- **Suppressions** (`app/modules/compliance`): do-not-contact adds an active row for the
  business, its domain and its phone; admins add by domain / phone / business and lift
  rows over `/suppressions`. Checked in the review queue, the leads list **and**
  classification, which skips a suppressed business entirely.
- **Classification respects decisions**: an approved opportunity is never overwritten and
  no second row is opened beside it; a service rejected, marked not-a-fit or duplicate
  within `REVIEW_COOLDOWN_DAYS` (90) is not re-created as pending; a `needs_enrichment`
  row is refreshed in place.
- **Endpoints**: `GET /review-queue` (grouped by business, strongest first, weak signals
  under `REVIEW_WEAK_CONFIDENCE` hidden unless `include_weak=true`, with a hidden count;
  filters `service`, `city`, `state`, `industry`, `min_score`, `q`, `status`), `GET
  /review-queue/{business_id}` (facts with field provenance, latest audit, AI summary
  flagged `ai_generated: true`, every opportunity with evidence, score components, AI
  provenance and decision history), `GET /leads` (approved and unsuppressed; a sales rep
  sees only `assigned_to = me`), `GET /users?role=sales_rep` for reviewers.
- **`make seed-demo-users`** (development only): `reviewer@`, `rep1@`, `rep2@`,
  `crm@example.com` with `DEMO_USERS_PASSWORD` from `.env`.
- **Frontend**: typed API client generated from `openapi.json` with `openapi-typescript`
  (`make api-types`; `make check` fails on drift), one `api.ts` wrapper that attaches the
  in-memory access token, refreshes **once** on 401 and sends the browser to `/login` when
  that fails; role-aware shell and navigation; pages `/login`, `/review` (filters, weak
  toggle, status tabs, batch reject / not-a-fit), `/review/[businessId]` (facts, audit,
  AI summary box, opportunity cards with score bars, decision dialogs, do-not-contact with
  confirmation, history with Undo, toast with Undo, next/previous, `j` `k` `a` `r` `?`
  shortcuts that never fire in a field), `/duplicates`, `/leads`, `/admin/suppressions`.
- **Safety in the UI, tested**: page text, evidence and AI output are only ever rendered as
  text (a repo-wide test forbids `dangerouslySetInnerHTML`); only `http(s)` URLs become
  links, with `target="_blank" rel="noopener noreferrer"`; every AI-touched field carries
  "AI-generated — verify before use"; leads show business-level public phone and website
  only.
- **Tests**: 52 backend tests (decisions and required fields, RBAC matrix, 409 on a stale
  lock, batch limits, undo window and who may undo, suppression effects on queue / leads /
  classification, cool-down, assignee validation, demo users) and 56 frontend tests
  (Vitest + Testing Library). `make e2e` runs the Playwright smoke against the demo stack.

### Changed

- `opportunities` gained `decided_at`, `decided_by` and `lock_version`; `OpportunitySummary`
  exposes `lock_version`, the detail `decided_at` / `decided_by`.
- Re-classification updates `needs_enrichment` rows in place (previously only `pending`).
- `GET /users` accepts `role` and `is_active` filters; a reviewer may call it with
  `role=sales_rep` only.
- API version `0.6.0`.

## [v0.5.0] - 2026-09-20

### Added

- **Opportunities** (`app/modules/opportunities`, migration `0005`). Every audited business
  gets, per service that fits, one *pending* opportunity carrying the reason, the verbatim
  evidence (finding code, text, URL), a confidence, a four-part score and its provenance.
  The four services live as data in `catalogue.py`: `website_design`, `seo_gbp`,
  `booking_setup`, `ads_social`. A partial unique index keeps one pending opportunity per
  business and service, so re-classification updates in place and never duplicates.
- **Deterministic rules first** (`rules.py`). Findings map to services; a service's
  confidence is `1 - prod(1 - c)` over its findings' base confidences by severity
  (high 0.8, medium 0.6, low 0.4, info 0.2), capped at 0.95. `ads_social` is the one weak
  signal from a check (no social links on a parsed page, fixed 0.3). Robots-blocked and
  permanently closed businesses get nothing; an unreachable site yields `website_design`
  only. Rules need no model and run under every provider, including `disabled`.
- **AI second, never trusted** (`app/modules/ai`). A provider-agnostic `LLMClient` with an
  `OpenAIClient` (official SDK, strict JSON-schema output, the SDK's timeout and 429/5xx
  retries, metered into `api_calls` under a new `openai` source row) and a `FakeLLMClient`
  that answers from `app/demo/ai/`. The prompt is checked in as `classify_v1.md`
  (`classify-1`): page text is untrusted data inside delimiters the page cannot close, only
  catalogue services may be proposed, every claim must quote its input verbatim, unknown
  stays `unknown`, and no person, email, phone, address, budget, date or intent may be
  invented. **What is sent is minimised**: name, industry, city/state, website, the
  findings with their evidence, the PageSpeed score, the tech stack and up to 8 000 chars
  of page text — with phone numbers, emails and street addresses scrubbed from every
  string first, including ones printed on the page.
- **Guardrails on every answer** (`guardrails.py`, one test per rule). A quote that is not
  a whitespace-normalised substring of the input is dropped; an unknown finding code drops
  the evidence item; an unknown service drops the opportunity; an opportunity with no
  valid evidence left is dropped; an AI-only opportunity is capped at 0.6 and marked
  `source = ai`; a rationale or summary carrying an email, a phone number or a URL that was
  never sent is blanked; forbidden wording ("needs", "should", "bad", "terrible",
  "outdated website") blanks the rationale; `buying_intent = explicit` survives only when a
  valid quote matches a configured pattern; `needs_human_review` is always `true`; an
  industry outside the taxonomy becomes `unknown`. Every drop is recorded in
  `rejected_claims` on the classification row.
- **Merging**: the AI may confirm a rule (raising its confidence by at most 0.15, with
  valid evidence) or add a service the rules missed. It can never remove a rule
  opportunity: a service the AI omits stays, with `ai_agrees = false`.
- **Two-tier routing** (`routing.py`): every business goes to `AI_TRIAGE_MODEL`; it is
  escalated once to `AI_ESCALATION_MODEL` when the schema is still invalid after one retry,
  a kept confidence lands in 0.40–0.60, the audit flagged a JavaScript shell, or the
  industry does not match the listing. The escalation answer replaces the triage answer;
  both are stored. `AI_ESCALATION_ENABLED=false` leaves unclear cases as they are.
- **Cost controls** (`budget.py`): a daily USD budget and call cap (Redis, UTC day), a
  per-run cap, an estimated cost from configured per-million prices (`null` when prices are
  not set, with a logged warning), and **reuse by input hash** — the same prompt version,
  model and input is never sent twice (`status = reused`, zero tokens). When a guard trips
  the AI is skipped (`status = skipped_budget`), rule opportunities are still created and
  the run finishes `done`.
- **`ai_classifications`**: model, prompt version, input hash, status
  (`ok | schema_invalid | guardrail_trimmed | error | skipped_budget | skipped_disabled |
  reused`), escalated, validated `output`, `raw_output` (≤ 20 KB), `rejected_claims`,
  tokens, estimated cost, latency, error. `raw_output` and `output.business_summary` expire
  with `AUDIT_CONTENT_TTL_DAYS`: `make purge-expired` nulls them and keeps the rest.
- **Scoring `scoring-1`** (`scoring.py`): `facts` (operational, website state known,
  industry known, city + state), `inference` (the final confidence), `intent` (1 only for a
  guardrail-passed explicit intent), `contactability` (a public business phone, and a
  contact form or email link on the homepage — business-level only). Total is
  `0.25·facts + 0.45·inference + 0.10·intent + 0.20·contactability`, weights in settings,
  and the components are stored and returned with every opportunity.
- **The `classification` job**, queued automatically when an audit run finishes and keyed
  off it. One business failing never fails the run (savepoint per business); an AI failure
  of any kind leaves the rules standing. `result_summary` reports `{businesses,
  opportunities_created, opportunities_updated, ai_calls, ai_escalations, ai_reused,
  ai_skipped_budget, ai_errors, est_cost_usd}`.
- **Endpoints.** `GET /opportunities` (filters `service`, `review_status` — default
  `pending` — `min_score`, `industry`, `city`, `state`, `source`; sort by `score` or
  `created_at`; cursor pagination), `GET /opportunities/{id}` (reason, every evidence
  item, score components, AI provenance), `GET /businesses/{id}/opportunities`,
  `POST /businesses/{id}/classify` (`admin`, `reviewer`, `tech_admin`, idempotent),
  `POST /jobs/{id}/classify` (`admin`, `tech_admin`), `GET /ai/classifications/{id}` and
  `GET /ai/usage?date=` (`admin`, `tech_admin`). `sales_rep` reads opportunities and gets
  403 on the rest.
- **Demo.** Six scripted model answers under `app/demo/ai/` cover a clean answer, schema
  drift then a valid retry, an invented quote, an invented email, a prompt-injection
  homepage (new demo site `riversideplumbing.invalid`, new listing `demo-e14`) and an
  unclear case that escalates. `expected_opportunities.json` records what every demo
  business must end up with; `make load-demo-data` now runs classification too and ends
  with scored opportunities, with no key and no network call.
- **`make ai-smoke`** — the second and only other command that talks to a live external
  API, run by a human: one real OpenAI call for one demo business, printing the validated
  output, the rejected claims, the tokens and the estimated cost. It stores nothing.
- New settings in `.env.example`: `AI_PROVIDER`, `OPENAI_API_KEY`, `AI_TRIAGE_MODEL`,
  `AI_ESCALATION_MODEL`, the four price variables, `AI_ESCALATION_ENABLED`,
  `AI_DAILY_BUDGET_USD`, `AI_MAX_CALLS_PER_RUN`, `AI_DAILY_CALL_CAP`, `AI_RPS`,
  `AI_PAGE_TEXT_MAX_CHARS`, `AI_TIMEOUT_SECONDS`, `AI_MAX_RETRIES`,
  `AI_RAW_OUTPUT_MAX_CHARS`, `AI_EXPLICIT_INTENT_PATTERNS`, `SCORING_WEIGHT_*`.

### Changed

- `follow_up` now chains four runs: discovery → resolution → audit → classification.
- `purge-expired` reports a fourth count, the AI classifications whose raw output and
  summary it nulled.
- The demo dataset has 41 listings and 30 businesses (one more, for the injection case);
  `expected_audits.json` and `austin_plumbers.expected.json` moved accordingly.

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
