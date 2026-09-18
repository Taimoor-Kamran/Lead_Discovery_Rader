#!/usr/bin/env bash
# Shared helpers for spec scripts.
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
STATE_DIR="$ROOT/.claude/state"
ACTIVE_FILE="$STATE_DIR/active-spec"
mkdir -p "$STATE_DIR"

die()  { echo "✗ $*" >&2; exit 1; }
info() { echo "→ $*"; }
ok()   { echo "✓ $*"; }

spec_branch() {  # reads the "**Branch:** spec/..." line from a spec file
  grep -m1 -E '^\*\*Branch:\*\*|^Branch:' "$1" | sed -E 's/.*(spec\/[A-Za-z0-9._-]+).*/\1/'
}
spec_version() { basename "$1" .md; }
active_spec() { if [[ -f "$ACTIVE_FILE" ]]; then cat "$ACTIVE_FILE"; fi; }
has_remote() { git remote | grep -q '^origin$'; }
