# vX.Y.Z — <Title>

**Branch:** spec/vX.Y.Z-<slug>
**Depends on:** vA.B.C (must be merged to main)
**Estimate:** N–M working days
**Blueprint refs:** Section/slide numbers

## Goal
One or two sentences: what exists after this spec that did not exist before.

## Scope
- In scope item

## Out of scope
- Explicitly excluded item (prevents Claude from gold-plating)

## Human prerequisites
(Plain bullets, NOT checkboxes — things only a person can do. Do these before `spec-run.sh`.)
- e.g. Create API key X and put it in `.env` as `X_API_KEY`

## Design
Data model changes, endpoints, module layout, key decisions. Be concrete: table names,
column types, request/response shapes, env var names.

## Tasks
- [ ] Task 1 (small, verifiable)
- [ ] Task 2

## Acceptance criteria
(Testable statements. Each should map to at least one automated test.)
- [ ] AC1
- [ ] AC2

## Test plan
- Unit: …
- Integration: …
- Manual (human, before spec-finish): …

## Implementation notes
(Claude appends decisions made during implementation here.)

## Blockers
(Claude lists anything marked `- [~]` here, with what a human must do.)
