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

That stores the fixture as a discovery run, resolves it, and then audits every resulting
business's website — all in that one command, with no network call and no API key. It
prints each run id and its counts. Pass `ARGS=--queue-only` to hand the resolution run to
the worker instead and watch it with `GET /api/v1/jobs/{id}/status`.

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

### The demo websites

`make load-demo-data` also runs the website audits, so it leaves you with 29 businesses
*and* 28 audits. The demo businesses' domains have checked-in websites under
`backend/app/demo/sites/<host>/`, and the audit fetcher answers from those files: **no
request leaves the machine and no API key is involved.** Between them they cover a modern
site with a booking widget, an http-only site with a 2016 copyright, WordPress with no
meta description, Wix / Square / GoDaddy builder sites, a Shopify store, a `robots.txt`
that disallows everything, a `robots.txt` that answers 503, an http→https redirect chain,
a host that cannot connect, a JavaScript shell, a certificate that does not verify, and a
PageSpeed quota error.

```
GET /api/v1/businesses?finding=no_online_booking
GET /api/v1/businesses?audit_status=robots_blocked
GET /api/v1/businesses/{id}/audits
GET /api/v1/website-audits/{id}
```

`backend/app/demo/sites/expected_audits.json` records the status and finding codes every
demo domain must produce, and `test_demo_audits.py` asserts them — so a change to a check
or a threshold fails the suite rather than quietly producing a different answer.

## How the website audit works

Every business that has a website of its own gets a **deterministic, evidence-backed audit
of its homepage**. Audits run automatically after each resolution run, and can be asked for
by hand at any time. Nothing in the audit is an opinion: each finding carries the verbatim
text it was read from and the URL it was read at, so a salesperson can open the page and
point at it.

```
discovery -> resolution -> audit
```

One audit, in order:

1. **What to fetch.** A business whose listing has no website, or only a social profile,
   is audited with **zero network calls** — the finding is about the listing
   (`no_website`, `social_profile_only`), not about a page.
2. **robots.txt first**, fetched through the same guard as everything else and cached per
   host for 24 hours. If it disallows us the homepage is **never requested** and the audit
   ends as `robots_blocked`, with no findings about the site's content.
3. **The homepage, once.** No crawling, no sitemap, no second page, no JavaScript. A
   page that builds itself with JavaScript is flagged (`js_shell_suspected`) rather than
   rendered, so nobody mistakes an empty shell for an empty website.
4. **The deterministic checks**: reachability and the redirect chain, HTTPS and the
   certificate, the mobile viewport, title, meta description, `h1`, LocalBusiness
   structured data, favicon, phone/email/contact-form presence, booking and shop
   signatures, linked social platforms, the tech stack and the copyright year.
5. **PageSpeed Insights (mobile)** for the performance score, LCP, CLS, TBT and the CrUX
   category. PageSpeed is a bonus, not a dependency: if it is out of quota or down, the
   audit still finishes with `psi = null` and `checks.psi_error` saying why.
6. **Findings.** Each gap becomes a coded finding with a severity, the service category it
   points at (`web_design`, `seo`, `booking`, `performance`, `ecommerce`, `security`,
   `web_presence`), a neutral message, and its evidence.

Wording is enforced by a test over the whole catalogue: every message begins with "Audit
found", "Audit could not", "PageSpeed" or "Listing shows", and may never contain "needs",
"should", "bad", "terrible" or "outdated website". `Audit found no online booking or
scheduling link on the homepage.` is a fact the owner can check; "needs a new website" is
a sales pitch the data does not support.

### Reading audits

```
GET  /businesses?finding=no_online_booking&finding=no_https   # AND, on the newest audit
GET  /businesses?audit_status=robots_blocked
GET  /businesses/{id}/audits                                  # history, newest first
GET  /website-audits/{id}                                     # checks, findings, PSI, tech
POST /businesses/{id}/audit                                    # a fresh audit now
POST /jobs/{resolution_run_id}/audit                           # that run's businesses
```

Each item in `GET /businesses` carries a `latest_audit` of `{status, finding_codes,
audited_at}`; `null` means never audited, which is not the same as an audit that found
nothing. `page_text` on an audit — the visible text kept as input for the v0.5.0 AI step —
is readable by `admin`, `reviewer` and `tech_admin` only; other roles see
`page_text_hidden: true`.

Retention: `make purge-expired` nulls `page_text` once `AUDIT_CONTENT_TTL_DAYS` (90) has
passed. The checks, the findings and their evidence snippets are our own observations and
stay. Full HTML is never stored — only a SHA-256 of it.

## What the bot does and doesn't do

The audit identifies itself as `LeadDiscoveryRadarBot/0.4 (+BOT_CONTACT)`. Set
`BOT_CONTACT` in `.env` to a URL or an email address where a site owner who sees us in
their logs can reach a human.

**It does:**

- read `robots.txt` first, obey it, and cache it for 24 hours;
- fetch **one page** — the homepage the business's own listing gives — over `http` or
  `https`, on port 80 or 443 only;
- verify TLS certificates, always. A certificate that does not verify is reported as
  `tls_invalid` and the request is **never** retried with verification off;
- wait at least `AUDIT_HOST_THROTTLE_SECONDS` (5) between two requests to the same host,
  and keep at most `AUDIT_MAX_CONCURRENCY` (4) fetches in flight across the whole system;
- stop reading at `AUDIT_MAX_BYTES` (2 MB) and mark the result `truncated`;
- refuse, before connecting, any URL that resolves to a private, loopback, link-local,
  multicast, reserved, unspecified or CGNAT address — including via a redirect.

**It does not:**

- crawl. No second page, no sitemap, no link following beyond validated redirects;
- run JavaScript, take screenshots, or store the HTML it read;
- log in, submit a form, click anything, or interact with a site in any way;
- touch social media. A social profile is *recorded* from the listing and from links on
  the homepage; it is never fetched, scraped or read;
- collect owner or personal contact details. Only the *presence* of a business phone
  link, email link or contact form is recorded, plus one example of each — never a
  harvested list;
- send anything to anybody. This system has no outreach of any kind.

If a site owner asks to be left out, add a `Disallow: /` for
`LeadDiscoveryRadarBot` — or for `*` — and the next audit records `robots_blocked` and
reads nothing.

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

## Recomputing businesses

A business row holds no facts of its own: every value on it is the survivorship winner
among the field values beneath it. So when the survivorship rules change, existing rows
still show what the *old* rules decided, and nothing else would ever revisit them.

```bash
make recompute-businesses                                    # every business
make recompute-businesses ARGS="--business-id <uuid>"        # just one
```

It is batched and idempotent, and prints `changed / unchanged` counts — a business that
already agrees with the rules is left alone.

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
