#!/usr/bin/env bash
# Claude Code Stop hook: blocks Claude from finishing until the active spec is complete.
# Complete = no "- [ ]" boxes left in the spec AND `make check` passes.
# Safety cap: after SPEC_GATE_MAX_BLOCKS consecutive blocks it lets Claude stop and hands back to you
# (this replaces the usual stop_hook_active check, which would only allow a single continuation).
set -uo pipefail
cat > /dev/null   # drain the hook's JSON payload from stdin
ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
STATE="$ROOT/.claude/state"; ACTIVE="$STATE/active-spec"; COUNT_FILE="$STATE/stop-count"
MAX="${SPEC_GATE_MAX_BLOCKS:-25}"

[[ -f "$ACTIVE" ]] || exit 0                       # no spec in progress → allow stop
SPEC="$ROOT/$(cat "$ACTIVE")"; [[ -f "$SPEC" ]] || exit 0

COUNT=$(cat "$COUNT_FILE" 2>/dev/null || echo 0)
if (( COUNT >= MAX )); then
  echo "spec-stop-gate: reached $MAX consecutive blocks — letting Claude stop. Human attention needed." >&2
  rm -f "$COUNT_FILE"; exit 0
fi

block() {
  echo $((COUNT + 1)) > "$COUNT_FILE"
  jq -n --arg r "$1" '{decision: "block", reason: $r}'
  exit 0
}

UNTICKED="$(grep -nE '^\s*- \[ \]' "$SPEC" | head -15)"
if [[ -n "$UNTICKED" ]]; then
  block "The spec $(basename "$SPEC") is not finished. Unticked items remain:
$UNTICKED
Keep implementing. If an item is truly blocked on a human (credentials, a decision), mark it '- [~]' and explain it under '## Blockers'."
fi

if [[ -f "$ROOT/Makefile" ]]; then
  OUT="$(cd "$ROOT" && timeout 840 make check 2>&1)"; RC=$?
  if (( RC != 0 )); then
    block "All boxes are ticked but 'make check' fails (exit $RC). Fix it, and untick any box whose claim is no longer true. Last output:
$(echo "$OUT" | tail -60)"
  fi
fi

rm -f "$COUNT_FILE"
exit 0
