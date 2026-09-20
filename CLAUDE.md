# CLAUDE.md — Lead Discovery Radar

You are building the **Lead Discovery Radar MVP**: an internal, human-in-the-loop lead-intelligence
pipeline (Discover → Process → Intelligence → Verification → CRM). Source of truth for *what* to build
is the active spec in `specs/`. Source of truth for *why* is the Technical Blueprint v1.0.

## How you work (spec-driven development)

1. Read `.claude/state/active-spec` → it contains the path of the spec you are implementing (e.g. `specs/v0.2.0.md`).
2. Read that spec completely before writing code. Also skim earlier merged specs for context.
3. Work through the **Tasks** checklist top to bottom. Tick each box (`- [ ]` → `- [x]`) in the spec file
   **only after** the code exists and its tests pass.
4. Tick **Acceptance criteria** boxes only after you have verified each one (by a test where possible).
5. Run `make check` often. Do not finish while any box is unticked or `make check` fails —
   a Stop hook enforces this.
6. Commit in small, logical commits on the current branch using Conventional Commits,
   prefixed with the spec version: `feat(v0.2.0): add Google Places adapter`.
7. **Never** switch to, commit to, merge into, or push `main`. Never run `git push --force`. Never create tags.
   Merging and tagging are done by a human with `scripts/spec-finish.sh`.
8. If something in the spec is ambiguous, pick the most conservative reasonable option, write it
   under `## Implementation notes` at the bottom of the spec, and keep going.
9. If something is **genuinely blocked** (missing credential, needs a human decision), write it under
   `## Blockers` in the spec, change the related box to `- [~]` (blocked), and continue with everything else.
   `- [~]` does not block the Stop hook; `- [ ]` does.
10. Do not edit the scope of a spec (Goal / Scope / Out of scope). Only tick boxes and append notes.

## Stack (do not change without a spec)

- Backend: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.x, Alembic, HTTPX, BeautifulSoup + lxml
- Workers: RQ + Redis
- DB: PostgreSQL 16 (JSONB for raw payloads)
- Frontend: Next.js (App Router) + React + TypeScript + Tailwind
- AI: provider-agnostic LLMClient (app/modules/ai); OpenAI implemented, model names from env vars; Fake provider for tests/demo
- Tooling: uv (Python deps), ruff (lint+format), mypy (types), pytest, respx (HTTP mocking), pnpm (frontend)
- Runtime: Docker Compose (api, worker, web, postgres, redis)

## Repository layout

```
backend/app/
  core/          config, db, security, logging, errors
  modules/
    auth/ jobs/ sources/ adapters/ normalization/ resolution/
    discovery/ businesses/ audit_web/ opportunities/ audit/ ai/
    scoring/ review/ crm/ compliance/   (audit/ = audit log; audit_web/ = website audits)
  workers/       RQ task entrypoints
backend/migrations/  Alembic
backend/tests/   unit/ integration/ fixtures/  (recorded API responses live in fixtures/)
frontend/
specs/
scripts/
```

## Non-negotiable rules (from the blueprint — these override any convenience)

- **Permitted sources only.** Official APIs + fetching a business's public homepage within robots.txt.
  No social-media scraping (LinkedIn, Meta, TikTok, X, Reddit). No logins, no CAPTCHA bypass, no anti-bot evasion.
- **Every fetch of a business website goes through `app/core/safe_fetch.py`** (SSRF guard, robots.txt); official APIs go through `app/core/http.py`.
- **Provenance is mandatory.** Every stored fact carries `source`, `source_url`, `source_record_id`,
  `discovered_at`, and where relevant `evidence_text` + `confidence`.
- **Never invent data.** Unknown is `null` / `"unknown"`, never a guess. This applies to code *and* AI prompts.
- **Human review gate.** Nothing is exported to the CRM unless an opportunity has `review_status = approved`
  by a human user. Enforce in the service layer AND with a test.
- **No outreach automation.** The system never sends emails, SMS, DMs or calls.
- **No owner/personal contact data** in the MVP. Business-level public phone/email only.
- **Secrets** come from environment variables only. Never commit `.env`. Never log secrets.
- **Tests never hit live external APIs.** Use recorded fixtures + respx. Live calls only in
  `scripts/` smoke tools that a human runs manually.

## Definition of done (every spec)

- All Tasks and Acceptance boxes ticked (or `- [~]` with a written blocker)
- `make check` passes (ruff, mypy, pytest; frontend lint/typecheck/test once frontend exists)
- New Alembic migration(s) apply cleanly on an empty DB and downgrade cleanly
- New env vars documented in `.env.example`
- `CHANGELOG.md` has an entry under the spec's version
