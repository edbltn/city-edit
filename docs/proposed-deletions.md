# Proposed deletions — awaiting sign-off

**Nothing in this list has been deleted.** It is a proposal, with the evidence
for each entry, so the call is yours. Everything here is recoverable from git
history whatever you decide.

Delete this file once the list is resolved.

## How the list was built

Two passes:

1. A script scanned every markdown file for backtick-quoted source paths and
   checked whether a file of that basename exists anywhere in the repo. Docs
   that reference code which no longer exists are the ones a new contributor
   trips over first.
2. Manual review of anything that reads as a one-off artifact rather than
   reference material.

Reproduce pass 1:

```bash
python3 - <<'PY'
import re, pathlib, os
pat = re.compile(r'`([A-Za-z0-9_./-]+\.(?:py|ts|tsx|sh|lua|js))`')
for f in sorted(pathlib.Path("docs").rglob("*.md")):
    missing = [m for m in set(pat.findall(f.read_text(errors="ignore")))
               if not [h for h in pathlib.Path(".").rglob(os.path.basename(m))
                       if ".git/" not in str(h) and "node_modules" not in str(h)
                       and "/env/" not in str(h)]]
    if missing: print(f"{f} -> {', '.join(sorted(missing))}")
PY
```

---

## Recommended — delete

| Path | Lines | Last touched | Why |
|---|---:|---|---|
| `docs/archive/roam.md` | 567 | 2026-06-14 | A design spec for a "Roam" routing engine that was **never built**. Every file it specifies (`roam_router.py`, `roam_cache.py`, `tiles.py`, `refresh.py`) is absent and its implementation checklist is entirely unchecked. |
| `docs/archive/vote-to-heatmap-pipeline.md` | 673 | 2026-06-14 | Documents the removed segment/node vote model (`desire_path_voting.py`, the `segment_votes`/`node_votes` hashes). Cited line numbers are stale. Superseded by `voting-architecture.md`. |
| `docs/archive/vote-system-design.md` | 359 | 2026-06-14 | The 2026-06-18 block design whose central decision — fan a vote across every edge of its block at write time — was **reversed**. Its Redis dedup structures did ship and are documented accurately in `three-layer-model.md`. |
| `docs/archive/cluster-model.md` | 163 | 2026-06-14 | The "everything is a Cluster" abstraction built on that same reversed propagate model. |
| `docs/archive/unify-voting-waves.md` | 254 | 2026-06-14 | Agent delegation prompts for the two docs above. Its own archive entry warns the prompts "must not be reused". |
| `docs/archive/README.md` | 16 | 2026-06-14 | The index for the above; goes with them. |
| `server/streetscape_blocks/COMPARISON.md` | 64 | 2026-06-17 | Compares `build_blocks_generic.py` against `build_nyc_blocks.py` — **neither file exists**, nor does the `compare_blocks.py` harness that produced the numbers. The findings that still matter (median IoU 0.84, and the `HALF_WIDTH` calibration they justify) are now preserved in `server/streetscape_blocks/README.md`. |
| `deploy_20260805.md` | 527 | 2026-08-05 | A single deploy's working log, left at the repo root. Records one revision, one digest, one asset hash — all superseded. The repeatable procedure lives in `docs/gcp-deployment.md`; the durable history is `changelog/`. |
| `.claude/understand-multicity-maps.md` | 35 | — | Session artifact from a `/understand` run, not documentation. |
| `.claude/understand-prod-perf-osrm.md` | 56 | — | Same. |
| `.claude/understand-sf-map-fixes.md` | 36 | — | Same. |
| `.claude/understand-subdomains.md` | 26 | — | Same. |

**Total: 2 776 lines across 12 files.**

The `docs/archive/` block is the substantive one (2 032 lines). The argument for
deleting rather than keeping it: this repo is now aimed at outside contributors,
and five detailed documents describing a data model that was *deliberately
reversed* are worse than no documents. A contributor who finds
`vote-system-design.md` and implements its propagate-and-dedup write path has
been actively misled by the repo. The archive README's warning only helps
someone who arrives via the README.

A cheaper alternative if you'd rather keep them: they are already excluded from
the published site (`exclude_docs` in `mkdocs.yml`), so a browsing reader will
not meet them. Deleting is still cleaner — git history is the archive.

**Before deleting `docs/archive/`**, two files link into it and need one-line
edits: `docs/README.md` (the `archive/` table row) and `docs/three-layer-model.md`
(its opening reference to the superseded design).

## Needs a decision, not a recommendation

| Path | Lines | Why it's ambiguous |
|---|---:|---|
| `TODO.md` | 66 | Two-thirds is a "Recently Resolved" list of things that shipped, so as a TODO it is misleading. But the four genuinely open items (nginx cache headers, Redis HA, and two features) aren't recorded anywhere else. **Suggest: trim to the open items rather than delete** — or move them to GitHub Issues, which is where a public repo's contributors will look. |
| `docs/agents.md` | 437 | Accurate as history, but references three scripts that no longer exist (`import_citibike.py`, `route_proposals.py`, `server/blocks.py`). **Suggest: keep, fix the three references.** It is the only record of how the agent fleets were run. |

## Recommended — keep

For completeness, since these were considered:

- **`docs/story.md`** (458 lines) — narrative history written to be quarried for
  talks, posts and grant copy. Every claim traceable to a commit. Its two stale
  references are to code the story is explicitly recounting the removal of.
- **`docs/course/`** — an interactive curriculum with a live progress ledger.
  Already excluded from the published site; still valuable in-repo.
- **`changelog/`** (146 reports) — the durable engineering history. The algorithm
  dossiers cite these directly for their failure modes; they are load-bearing.
- **`server/streetscape_blocks/eval/`** — holds `manhattan_edges.npz`, a fixture,
  not a document. Out of scope for this pass, but worth a look: the harness that
  used it is gone.
