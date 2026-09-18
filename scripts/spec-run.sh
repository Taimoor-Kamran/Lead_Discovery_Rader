#!/usr/bin/env bash
# Usage:
#   scripts/spec-run.sh                      # interactive Claude session; you approve risky commands
#   scripts/spec-run.sh --auto               # headless, no permission prompts — ONLY inside a container/VM
#   scripts/spec-run.sh "extra instruction"  # follow-up pass with extra guidance
source "$(dirname "$0")/_lib.sh"
command -v claude >/dev/null || die "Claude CLI not found"
command -v jq >/dev/null || die "jq is required by the hooks"

AUTO=0
if [[ "${1:-}" == "--auto" ]]; then AUTO=1; shift; fi
EXTRA="${1:-}"

SPEC="$(active_spec)"; [[ -n "$SPEC" ]] || die "no active spec — run spec-start.sh first"
BRANCH="$(spec_branch "$ROOT/$SPEC")"
[[ "$(git branch --show-current)" == "$BRANCH" ]] || die "you are not on $BRANCH"
rm -f "$STATE_DIR/stop-count"

PROMPT="Implement the spec at $SPEC, following CLAUDE.md exactly.
Read the whole spec first, then work through every Task in order. Tick each checkbox in the spec
file only when its code exists and its tests pass. Verify every Acceptance criterion and tick it.
Run 'make check' frequently. Commit in small Conventional Commits prefixed with the spec version.
Do not stop until every box is ticked (or marked '- [~]' with a written blocker) and 'make check' passes.
Never touch the main branch; never push, merge or tag."
if [[ -n "$EXTRA" ]]; then PROMPT="$PROMPT

Additional instruction for this pass: $EXTRA"; fi

cd "$ROOT"
if [[ $AUTO -eq 1 ]]; then
  info "headless run on $BRANCH (no permission prompts)"
  claude -p "$PROMPT" --dangerously-skip-permissions --output-format text | tee "$STATE_DIR/last-run.log"
else
  info "interactive run on $BRANCH"
  claude "$PROMPT"
fi
