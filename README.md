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
