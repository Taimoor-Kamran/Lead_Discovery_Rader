#!/usr/bin/env bash
# Claude Code PreToolUse hook (matcher: Bash). Blocks git operations that belong to humans.
# Exit code 2 blocks the tool call and shows stderr to Claude.
set -uo pipefail
CMD="$(jq -r '.tool_input.command // ""')"
deny() { echo "git-guard: blocked — $1. Merging, tagging, pushing and touching main are done by a human via scripts/spec-finish.sh." >&2; exit 2; }

echo "$CMD" | grep -qE '(^|[;&|[:space:]])git[[:space:]]+push'   && deny "git push"
echo "$CMD" | grep -qE '(^|[;&|[:space:]])git[[:space:]]+merge'  && deny "git merge"
echo "$CMD" | grep -qE '(^|[;&|[:space:]])git[[:space:]]+tag'    && deny "git tag"
echo "$CMD" | grep -qE '(^|[;&|[:space:]])git[[:space:]]+rebase' && deny "git rebase"
echo "$CMD" | grep -qE 'git[[:space:]]+reset[[:space:]].*--hard'  && deny "git reset --hard"
echo "$CMD" | grep -qE 'git[[:space:]]+branch[[:space:]].*-D'     && deny "git branch -D"
echo "$CMD" | grep -qE 'git[[:space:]]+(checkout|switch)[[:space:]]+(-[a-zA-Z]+[[:space:]]+)?main([[:space:];&|]|$)' && deny "switching to main"
echo "$CMD" | grep -qE 'git[[:space:]]+checkout[[:space:]]+(--[[:space:]]+)?\.([[:space:]]|$)' && deny "git checkout . (discards uncommitted work)"
echo "$CMD" | grep -qE 'git[[:space:]]+restore[[:space:]]'              && deny "git restore (discards uncommitted work)"
echo "$CMD" | grep -qE 'git[[:space:]]+clean[[:space:]].*-[a-zA-Z]*f'   && deny "git clean -f"
echo "$CMD" | grep -qE 'git[[:space:]]+stash[[:space:]]+(drop|clear)'  && deny "git stash drop/clear"
if echo "$CMD" | grep -qE 'git[[:space:]]+commit'; then
  BR="$(git -C "${CLAUDE_PROJECT_DIR:-.}" branch --show-current 2>/dev/null)"
  [[ "$BR" == "main" ]] && deny "committing on main"
fi
exit 0
