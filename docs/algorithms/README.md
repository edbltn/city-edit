# Algorithm dossiers

City Edit runs on a handful of bespoke algorithms that no library gave us: how a
street network is cut into clickable blocks, how two different families of "top
proposal" are elicited from raw votes, how support becomes colour. This
directory documents each one **end to end, in pseudocode**, at the altitude the
source comments can't reach — a comment explains its own function, a dossier
explains the pipeline that function is step 4 of.

Start here if you want to change behaviour, not just fix a bug.

| Dossier | The question it answers |
|---|---|
| [01 — Block identification](01-block-identification.md) | How does a raw OSM edge/node graph become the discrete, clickable "blocks" you vote on? |
| [02 — Top proposals (points)](02-top-proposals.md) | Of thousands of voted edges, which few get a square pin? |
| [03 — Top proposals (routes)](03-route-proposals.md) | How is the vote graph *explored* to find a hot corridor, and where does it stop? |
| [04 — Route finding](04-route-finding.md) | When you drop two waypoints, what decides the path between them? |
| [05 — Heat colouring](05-heat-coloring.md) | What number is a block's colour actually showing? |
| [06 — Sorting and ranking](06-ranking.md) | Why is this map first, and why is that proposal above this one? |
| [07 — Displayed counts](07-counts.md) | What exactly is a "voter", and what is a "block", in the numbers on screen? |

Related, and deliberately not duplicated here: [three-layer-model.md](../three-layer-model.md)
is the source of truth for *why* the graph/blocks/proposals split exists and for
vote write semantics; [voting-architecture.md](../voting-architecture.md) owns
vote storage and identity. Dossiers describe **computation**; those two describe
**structure and semantics**.

---

## The contract

Every dossier is machine-bound to the code it describes. `scripts/check_algorithm_docs.py`
re-checks the binding on every CI run, so a doc cannot drift silently — the
failure surfaces on the PR that moved the code, while the author still remembers
why they moved it.

This matters because it has already gone wrong: `server/streetscape_blocks/README.md`
spent months documenting two block generators (`build_blocks_generic.py`,
`build_nyc_blocks.py`) that had been replaced by a third. Nothing caught it. A
new contributor's first act would have been to run a file that isn't there.

### 1. Frontmatter declares the binding

```yaml
---
title: Block identification
description: One line. Material renders it as the page's meta description.
sources:
  - path: server/streetscape_blocks/build_blocks_graph_first.py
    anchors: [main, split_oversized, UnionFind]
  - path: server/block_votes.py
    anchors: [block_vote_arrays]
---
```

`anchors` are the symbols the pseudocode names. **Cite a symbol in prose → list
it as an anchor.** If someone renames or deletes it, the check fails and names
the doc. This is the highest-value rule here: it is decidable, has no false
positives, and catches the exact drift that made the streetscape README useless.

### 2. The knobs table is machine-checked

Every dossier tabulates its tuning constants in a table whose first column
header is `Knob`:

| Knob | Value | Defined in | What breaks if you change it |
|---|---|---|---|
| `TOP_PROPOSAL_MIN_SPACING_M` | `600` | `topProposals.ts` | Lower it and a hot avenue grows a stack of identical pins. |

The checker parses the first three columns and compares the documented value
against the literal in the source, so a knob tuned in code and not in prose is a
hard failure.

Two conveniences, because the table is read far more often than it is written:

- **"Defined in" may be a bare filename** — it is resolved against this doc's
  declared `sources`. A full repo path repeated down fifteen rows is an
  unreadable column, and the full paths are already in the frontmatter. An
  ambiguous basename is refused, not guessed.
- **Server knobs wrapped in `os.environ.get(NAME, "30")` are read at their
  baked-in default.** Mention the env override in prose; tabulate the default.

Write the fourth column as **what breaks**, not what the constant is. The value
is already in column two; "lower it and a hot avenue grows a stack of identical
pins" is the part a reader cannot derive.

### 3. Bound files point back

Every file listed under `sources` carries a banner in its first 60 lines:

```ts
// Algorithm doc: docs/algorithms/02-top-proposals.md
```

So the pointer works in both directions: a reader who opens the file finds the
dossier, and a reader who opens the dossier finds the file. The checker enforces
the banner's presence.

### 4. Coupling is a warning, not a gate

When a bound source file changes in a PR and its dossier does not, CI prints a
warning naming both. It does **not** block: plenty of edits — a rename, a perf
tweak, a type annotation — leave the algorithm intact, and a check that cries
wolf is a check people learn to ignore. Treat the warning as the prompt it is:
re-read the pseudocode, and either update it or move on.

---

## How to update a dossier

**Do this in the same commit as the code change.** A doc PR that trails the code
by a week is a doc PR that never lands.

1. Change the code.
2. Run `make docs-check`. Fix whatever it names.
3. Re-read the **Pseudocode** section of the affected dossier. Does it still
   describe what the code does? The checker verifies that symbols and constants
   exist — it cannot verify that step 3 still happens before step 4. That part
   is yours.
4. If you added a step, add it to the pseudocode and add its symbol to `anchors`.
5. If you added a tuning constant, add a knobs row. Say what **breaks** if it's
   changed, not what it is — `600` is already in the table; "lower it and a hot
   avenue grows a stack of identical pins" is the part a reader can't derive.
6. If the change came out of a real incident, add a line to **Failure modes**
   and link the `changelog/` report. The war stories are the most-read part of
   these documents.

```bash
make docs-check                                    # checks 1-4, offline
python3 scripts/check_algorithm_docs.py --base origin/main   # + coupling
python3 scripts/check_algorithm_docs.py --list-bindings      # what's bound
```

## How to add a dossier

Copy the skeleton of any existing one — they are deliberately uniform:

> frontmatter → **Why it exists** → **Inputs and outputs** → **Pseudocode** →
> **Tuning knobs** → **Invariants** → **Failure modes and history** →
> **Extension points**

Then add the banner to each source file, add the row to the table at the top of
this README, add the page to `nav:` in `mkdocs.yml`, and run `make docs-check`.

Two rules of altitude, learned from writing these:

- **Pseudocode, not transcription.** If the numbered steps read like the source
  with the types removed, the dossier will rot on the next refactor and helps
  nobody. Name the *decisions*: what is being traded off, what the alternative
  was, why the bound is where it is.
- **Don't move comments into the doc.** The in-source comments are excellent and
  they stay. A dossier earns its place by spanning files — the block bake is
  Python, the proposals are TypeScript, and the thing a contributor needs to
  understand crosses that boundary several times.
