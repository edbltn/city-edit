---
name: worktree
description: Create or land an isolated git worktree checkout for parallel agent work on a separate branch/PRD. Use when the user wants a new branch in its own directory, a separate checkout to work on a PRD, to spin up a parallel agent, or to rebase/merge a worktree's branch back into the main checkout. Triggers - "new worktree", "branch off for X", "separate checkout", "land this worktree", "rebase the worktree back".
---

# Worktree branching

This project uses git worktrees (no Docker) so multiple Claude agents can work
on different PRDs at once. Each worktree is a sibling directory named
`<repo>-<slug>-<hash>` on its own branch, sharing the main checkout's `.git`.

Two helper scripts do the work — prefer them over raw git commands:

- `scripts/new-worktree.sh <slug>` — create an isolated worktree
- `scripts/land-worktree.sh <dir>` — rebase the branch onto main and merge it back

## Deciding which action

- The user wants to **start** parallel work / a new branch / a separate
  checkout → **Create** (below).
- The user wants to **finish** / merge / rebase a worktree back into main →
  **Land** (below).

If it's ambiguous, run `git worktree list` first to see what exists, then ask.

## Create a worktree

1. Pick a short kebab-case `slug` from the task or PRD name (e.g. a PRD titled
   "Address autocomplete search" → `address-search`). Keep it under ~25 chars.
2. Run from the repo root:
   ```bash
   scripts/new-worktree.sh <slug>
   ```
   This creates `../<repo>-<slug>-<hash>` on branch `<slug>-<hash>`, copies the
   gitignored `server/.env`, runs `npm install`, builds the Python venv, and
   **starts the client dev server on the next free incremented port** (3001,
   3002, …) in the background, logging to `<dir>/dev-server.log`.
3. Report back to the user:
   - the new directory path and the `cd <dir> && claude` command (the user
     opens one terminal per worktree — do **not** launch a second Claude
     session yourself), and
   - the **dev server URL** the script printed (`http://localhost:<port>`) so
     they can preview the change immediately.
4. If you are an agent already working inside a worktree and the dev server is
   not running (e.g. it was stopped, or the worktree predates this behavior),
   start it yourself on the next free port and report the URL:
   ```bash
   # from <worktree>/client-react — pick the first free port at/after 3001
   port=3001; while lsof -iTCP:$port -sTCP:LISTEN >/dev/null 2>&1; do port=$((port+1)); done
   nohup npm run dev -- --port "$port" --strictPort > ../dev-server.log 2>&1 &
   echo "http://localhost:$port"
   ```
   Dev-mode detection (`client-react/src/config.ts`) keys off Vite's `DEV`
   flag, not the port, so any incremented port still talks to Flask on `:5001`.

## Land a worktree back into main

1. Identify the worktree directory (ask, or use `git worktree list`).
2. Make sure its work is committed — the script refuses to run on a dirty tree.
3. Run from the repo root:
   ```bash
   scripts/land-worktree.sh <worktree-dir>
   ```
   This rebases the branch onto `main` (linear history, no merge commit),
   fast-forwards `main` in the main checkout, then removes the worktree and
   deletes the branch.
4. If the rebase reports conflicts, surface the script's resolution
   instructions to the user, help resolve them in the worktree, then re-run the
   script. Never force-resolve or `--abort` without asking.
5. After a successful land, remind the user to `git push origin main` when ready
   — the script does not push.

## Notes

- `main` can only be checked out in one worktree, so landing always
  fast-forwards the main directory — that's by design.
- `server/osm_data/` (~1GB) is git-tracked and appears in each worktree for
  free; only `.env`, the venv, and `node_modules` need per-worktree setup, which
  the create script handles.
- The **client dev server** runs per-worktree on its own incremented port
  (3001, 3002, …), so multiple worktrees can preview the frontend in parallel.
  The backend stack is still single-instance: only one Flask/Redis/Postgres
  (ports 5001/6379/5432) and one full Docker stack (8080) can bind at a time,
  so all worktree dev servers share whatever backend is running on `:5001`.
  Fine for frontend/UI work; flag it to the user if a change needs an isolated
  backend.
- Stop a worktree's dev server with `pkill -f "vite.*--port <port>"`, or find
  it via the PID in `<worktree>/dev-server.log`.
