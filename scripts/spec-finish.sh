#!/usr/bin/env bash
# Usage: scripts/spec-finish.sh — gate checks, merge --no-ff into main, tag.
source "$(dirname "$0")/_lib.sh"
SPEC="$(active_spec)"; [[ -n "$SPEC" ]] || die "no active spec"
VERSION="$(spec_version "$SPEC")"
BRANCH="$(spec_branch "$ROOT/$SPEC")"
[[ "$(git branch --show-current)" == "$BRANCH" ]] || die "you are not on $BRANCH"
[[ -z "$(git status --porcelain)" ]] || die "uncommitted changes — commit them first"

info "gate 1/4: unticked boxes"
if grep -nE '^\s*- \[ \]' "$ROOT/$SPEC"; then die "spec has unticked boxes (listed above)"; fi
if grep -qE '^\s*- \[~\]' "$ROOT/$SPEC"; then
  echo "! spec has BLOCKED items:"; grep -nE '^\s*- \[~\]' "$ROOT/$SPEC"
  read -r -p "  merge anyway? [y/N] " a; [[ "$a" == "y" ]] || die "aborted"
fi

info "gate 2/4: make check"
if [[ -f "$ROOT/Makefile" ]]; then make -C "$ROOT" check || die "make check failed"; fi

info "gate 3/4: CHANGELOG"
grep -q "\[$VERSION\]" "$ROOT/CHANGELOG.md" 2>/dev/null || die "CHANGELOG.md has no [$VERSION] entry"

info "gate 4/4: human sign-off"
read -r -p "  Did you review the diff and test the feature by hand? [y/N] " a
[[ "$a" == "y" ]] || die "aborted — test first"

git checkout main
if has_remote; then git pull --ff-only origin main; fi
git merge --no-ff "$BRANCH" -m "merge: $VERSION ($BRANCH)"
git tag -a "$VERSION" -m "Spec $VERSION merged"
if has_remote; then
  git push origin main "$VERSION"
  git push origin "$BRANCH" || true
fi
rm -f "$ACTIVE_FILE" "$STATE_DIR/stop-count"
ok "$VERSION merged into main and tagged."
