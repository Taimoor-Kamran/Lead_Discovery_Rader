#!/usr/bin/env bash
# Usage: scripts/spec-start.sh v0.2.0
source "$(dirname "$0")/_lib.sh"
VERSION="${1:-}"; [[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "usage: spec-start.sh vX.Y.Z"
SPEC="specs/$VERSION.md"; [[ -f "$ROOT/$SPEC" ]] || die "$SPEC not found"
BRANCH="$(spec_branch "$ROOT/$SPEC")"; [[ -n "$BRANCH" ]] || die "no 'Branch:' line in $SPEC"

CUR="$(active_spec)"
if [[ -n "$CUR" && "$CUR" != "$SPEC" ]]; then
  die "another spec is active ($CUR). Finish it with spec-finish.sh or delete $ACTIVE_FILE"
fi
[[ -z "$(git status --porcelain)" ]] || die "working tree not clean — commit or stash first"

git checkout main
if has_remote; then git pull --ff-only origin main; fi

if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  info "branch $BRANCH exists — checking it out"
  git checkout "$BRANCH"
else
  git checkout -b "$BRANCH"
fi

echo "$SPEC" > "$ACTIVE_FILE"
rm -f "$STATE_DIR/stop-count"
ok "active spec: $SPEC  |  branch: $BRANCH"
echo "  next: scripts/spec-run.sh"
