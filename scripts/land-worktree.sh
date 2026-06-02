#!/bin/bash
#
# Rebase a worktree's branch onto main and fast-forward it into the main
# checkout, then tear the worktree down.
#
# Because worktrees share one .git, "main" is the same ref everywhere. We
# rebase the feature branch onto main (replaying its commits on top of the
# latest main), then fast-forward main — so history stays linear, no merge
# commits.
#
# Usage:
#     scripts/land-worktree.sh <worktree-dir>
#     scripts/land-worktree.sh ../city-edit-address-search-a1b2c3

set -euo pipefail

main_root="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"

wt="${1:?Usage: land-worktree.sh <worktree-dir>}"
wt="$(cd "$wt" && pwd)"   # absolute path
branch="$(git -C "$wt" rev-parse --abbrev-ref HEAD)"

if [ "$branch" = "main" ]; then
  echo "Refusing to land: worktree is on main, not a feature branch." >&2
  exit 1
fi

# Commit guard: don't silently drop uncommitted work.
if [ -n "$(git -C "$wt" status --porcelain)" ]; then
  echo "Worktree has uncommitted changes. Commit or stash them first:" >&2
  echo "    cd \"$wt\" && git add -A && git commit" >&2
  exit 1
fi

echo "==> Rebasing ${branch} onto main"
git -C "$wt" rebase main || {
  echo
  echo "Rebase hit conflicts. Resolve them in ${wt}:" >&2
  echo "    fix files, then: git add <files> && git rebase --continue" >&2
  echo "    or abort with:   git rebase --abort" >&2
  echo "Re-run this script once the rebase completes." >&2
  exit 1
}

echo "==> Fast-forwarding main -> ${branch}"
# Done from the main checkout. main can't be checked out in two worktrees,
# so it is guaranteed to live in the main_root checkout.
git -C "$main_root" merge --ff-only "$branch"

echo "==> Removing worktree and branch"
git -C "$main_root" worktree remove "$wt"
git -C "$main_root" branch -d "$branch"

cat <<EOF

==> Landed ${branch} into main at ${main_root}.

  Push when ready:
      cd "${main_root}" && git push origin main
EOF
