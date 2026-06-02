#!/bin/bash
#
# Create an isolated worktree checkout of this repo for a parallel agent.
#
# Each worktree lives in a sibling directory named:
#     <repo>-<slug>-<hash>
# on its own branch (same name minus the repo prefix), backed by the shared
# .git of the main checkout. Use one Claude session per worktree.
#
# Usage:
#     scripts/new-worktree.sh <slug>      # e.g. scripts/new-worktree.sh address-search
#     scripts/new-worktree.sh             # slug defaults to "wt"
#
# When done, land the branch back with: scripts/land-worktree.sh <dir>

set -euo pipefail

root="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
repo_name="$(basename "$root")"
parent="$(dirname "$root")"

slug="${1:-wt}"
# Short random hash so directories never collide, even with the same slug.
hash="$(openssl rand -hex 3)"
branch="${slug}-${hash}"
dest="${parent}/${repo_name}-${branch}"

echo "==> Creating worktree"
echo "    branch:    ${branch}"
echo "    directory: ${dest}"
git -C "$root" worktree add "$dest" -b "$branch"

# --- Carry over gitignored files the new checkout needs ---

if [ -f "$root/server/.env" ]; then
  echo "==> Copying server/.env (gitignored secrets)"
  cp "$root/server/.env" "$dest/server/.env"
fi

echo "==> Installing client deps (npm install)"
( cd "$dest/client-react" && npm install )

echo "==> Creating Python venv + installing deps"
(
  cd "$dest/server"
  python3 -m venv env
  # shellcheck disable=SC1091
  source env/bin/activate
  if command -v uv >/dev/null 2>&1; then
    uv pip install -r requirements.txt
  else
    pip install -r requirements.txt
  fi
)

# --- Start the client dev server on an incremented port ---
# The default dev port is 3000; each new worktree claims the next free port
# (3001, 3002, ...) so multiple worktrees can preview in parallel. Dev-mode
# detection in client-react/src/config.ts keys off Vite's DEV flag, not the
# port, so any port talks to Flask on :5001.

pick_port() {
  local p=3001
  while lsof -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; do
    p=$((p + 1))
  done
  echo "$p"
}

port="$(pick_port)"
log="${dest}/dev-server.log"
echo "==> Starting client dev server on port ${port} (logs: ${log})"
(
  cd "$dest/client-react"
  nohup npm run dev -- --port "$port" --strictPort >"$log" 2>&1 &
)

cat <<EOF

==> Ready.

  Client dev server running at:
      http://localhost:${port}
      (logs: ${log})

  Start an agent here:
      cd "${dest}" && claude

  Land this branch back into the main checkout when finished:
      ${root}/scripts/land-worktree.sh "${dest}"

EOF
