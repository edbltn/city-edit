#!/usr/bin/env python3
"""Verify that `docs/algorithms/*.md` still describes the code it claims to.

Algorithm dossiers are *bound* to their source: each declares the files it
documents, the symbols its pseudocode names, and the value of every tuning knob
it tabulates. This script re-checks those claims against the tree, so a doc
cannot quietly rot — the failure lands on whoever moved the code, while they
still remember why.

Checks (see docs/algorithms/README.md for the authoring contract):

  1. PATH      every `sources[].path` exists
  2. ANCHOR    every declared anchor still resolves in that file
  3. KNOB      every "Tuning knobs" row's documented value matches the source
  4. BANNER    every bound source file points back at its dossier
  5. COUPLING  (--base REF only) a bound source changed but its doc did not

1–4 are FAILures: they are decidable from the tree alone and have no false
positives. 5 is a WARNing — plenty of edits (a rename, a perf tweak) leave the
algorithm intact, and a check that cries wolf gets ignored.

Usage:
    python3 scripts/check_algorithm_docs.py                  # 1-4
    python3 scripts/check_algorithm_docs.py --base origin/main   # 1-5
    python3 scripts/check_algorithm_docs.py --list-bindings  # what's bound

No third-party dependencies: the frontmatter subset used here is hand-parsed so
the check runs on a bare `python3` in CI, a fresh clone, or a contributor's
laptop with no venv.
"""

import argparse
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join("docs", "algorithms")

# How far into a source file the "Algorithm doc:" banner may sit. Generous
# enough for a license header or a long import block, tight enough that the
# pointer is still the first thing a reader meets.
BANNER_WINDOW_LINES = 60
BANNER_MARKER = "docs/algorithms/"

# A knobs table is recognised by its header cell, so a doc can carry other
# tables freely.
KNOB_TABLE_HEADER = "knob"


# ── Frontmatter ─────────────────────────────────────────────────────────────
# The supported subset is exactly what a dossier needs:
#
#     ---
#     title: Block identification
#     sources:
#       - path: server/streetscape_blocks/build_blocks_graph_first.py
#         anchors: [split_oversized, UnionFind, PARALLEL_MAX_SEP_M]
#     ---
#
# Anything else (extra scalar keys) is read as a string and ignored.

def parse_frontmatter(text: str) -> dict:
    """Parse the leading `---` block. Returns {} when there isn't one."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    body = text[text.find("\n") + 1:end]

    meta: dict = {}
    sources: list[dict] = []
    current: dict | None = None
    in_sources = False

    for raw in body.split("\n"):
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())

        if indent == 0:
            in_sources = False
            key, _, val = line.partition(":")
            key, val = key.strip(), val.strip()
            if key == "sources":
                in_sources = True
                current = None
            elif key:
                meta[key] = val
            continue

        if not in_sources:
            continue

        stripped = line.lstrip()
        if stripped.startswith("- "):
            current = {}
            sources.append(current)
            stripped = stripped[2:].strip()
            if not stripped:
                continue
        if current is None:
            continue

        key, _, val = stripped.partition(":")
        key, val = key.strip(), val.strip()
        if key == "anchors":
            current["anchors"] = _parse_inline_list(val)
        elif key:
            current[key] = val

    meta["sources"] = sources
    return meta


def _parse_inline_list(val: str) -> list[str]:
    """`[a, b, c]` → ["a", "b", "c"]. Empty/absent → []."""
    val = val.strip()
    if val.startswith("[") and val.endswith("]"):
        val = val[1:-1]
    return [item.strip().strip("'\"") for item in val.split(",") if item.strip()]


# ── Anchor resolution ───────────────────────────────────────────────────────
# One symbol, several languages. Rather than parse Python and TypeScript, match
# the handful of shapes a *declaration* takes in either — a definition keyword,
# a binding keyword, or a bare assignment/property at the start of a line. That
# accepts every anchor these dossiers cite and rejects mere mentions in prose or
# call sites, which is the distinction that matters: a function that was deleted
# still appears in the comments that reference it.

def _anchor_patterns(name: str) -> list[re.Pattern]:
    n = re.escape(name)
    return [re.compile(p.format(n=n), re.MULTILINE) for p in (
        r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
        r"(?:def|class|function|interface|type|enum|struct)\s+{n}\b",
        r"^\s*(?:export\s+)?(?:const|let|var)\s+{n}\b",
        r"^\s*{n}\s*[:=]",
        r"^\s*(?:export\s+)?(?:async\s+)?{n}\s*[(<]",
    )]


def anchor_exists(source: str, name: str) -> bool:
    return any(p.search(source) for p in _anchor_patterns(name))


# ── Knob values ─────────────────────────────────────────────────────────────

def _rhs_of(source: str, name: str) -> str | None:
    """The right-hand side of `name`'s declaration, comments stripped."""
    n = re.escape(name)
    m = re.search(
        rf"^\s*(?:export\s+)?(?:const|let|var)?\s*{n}"
        rf"(?:\s*:[^=\n]+)?\s*=\s*(.+)$",
        source, re.MULTILINE)
    if not m:
        return None
    rhs = m.group(1)
    # Trailing statement punctuation and line comments carry no value.
    rhs = re.split(r"\s+(?://|#)\s", rhs)[0]
    return rhs.strip().rstrip(";").strip()


def documented_value_matches(source: str, name: str, expected: str) -> tuple[bool, str | None]:
    """Does `name`'s literal in `source` equal the documented `expected`?

    Returns (ok, actual). `actual` is None when the symbol has no assignment.
    """
    rhs = _rhs_of(source, name)
    if rhs is None:
        return False, None

    # Server knobs are env-overridable — `float(os.environ.get("X", "30"))`.
    # The BAKED-IN DEFAULT is what the doc tabulates (the env var is prose), so
    # read the fallback argument rather than the wrapper.
    env = re.search(r"os\.environ\.get\(\s*[^,]+,\s*([^)]+)\)", rhs)
    if env:
        rhs = env.group(1).strip()

    return _same_literal(rhs, expected), rhs


def _same_literal(a: str, b: str) -> bool:
    """Compare two literals the way a reader would: 600 == 600.0 == "600"."""
    def norm(s: str) -> str:
        return s.strip().strip("'\"").replace("_", "").strip()

    na, nb = norm(a), norm(b)
    if na == nb:
        return True
    try:
        return float(na) == float(nb)
    except ValueError:
        return False


def parse_knob_rows(text: str) -> list[tuple[str, str, str]]:
    """Extract (knob, value, path) from every "Knob | Value | Defined in" table."""
    rows: list[tuple[str, str, str]] = []
    in_table = False
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped.startswith("|"):
            in_table = False
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not in_table:
            if cells and cells[0].lower().lstrip("*` ").startswith(KNOB_TABLE_HEADER):
                in_table = True
            continue
        if set(stripped) <= set("|-: "):     # the header separator row
            continue
        if len(cells) < 3:
            continue
        knob, value, path = (_uncode(c) for c in cells[:3])
        if knob and value and path:
            rows.append((knob, value, path))
    return rows


def _uncode(cell: str) -> str:
    """Strip markdown emphasis/code fences from a table cell."""
    return cell.strip().strip("*").strip("`").strip()


def _resolve_basename(name: str, sources: set[str]) -> str | None:
    """Match a bare filename against the doc's declared source paths.

    Ambiguity is refused rather than guessed: two sources sharing a basename
    would make the knob's file genuinely undecidable, and silently picking one
    is how a check starts lying.
    """
    hits = [p for p in sources if os.path.basename(p) == name]
    return hits[0] if len(hits) == 1 else None


# ── Reporting ───────────────────────────────────────────────────────────────

class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def fail(self, doc: str, msg: str) -> None:
        self.failures.append(f"{doc}: {msg}")

    def warn(self, doc: str, msg: str) -> None:
        self.warnings.append(f"{doc}: {msg}")


def read(path: str) -> str | None:
    try:
        with open(os.path.join(REPO, path), encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def load_docs() -> list[tuple[str, str, dict]]:
    """(rel_path, text, frontmatter) for every dossier, in filename order."""
    docs = []
    root = os.path.join(REPO, DOCS_DIR)
    for name in sorted(os.listdir(root)):
        if not name.endswith(".md") or name == "README.md":
            continue
        rel = os.path.join(DOCS_DIR, name)
        text = read(rel) or ""
        docs.append((rel, text, parse_frontmatter(text)))
    return docs


def check_doc(rel: str, text: str, meta: dict, report: Report) -> set[str]:
    """Run checks 1-4 for one dossier. Returns the source paths it binds."""
    bound: set[str] = set()
    sources = meta.get("sources") or []
    if not sources:
        report.fail(rel, "no `sources:` in the frontmatter — every dossier must "
                         "declare the files it documents")
        return bound

    for entry in sources:
        path = entry.get("path")
        if not path:
            report.fail(rel, "a `sources:` entry has no `path:`")
            continue

        src = read(path)
        if src is None:                                          # 1. PATH
            report.fail(rel, f"documents `{path}`, which does not exist "
                             f"(moved or deleted?)")
            continue
        bound.add(path)

        for anchor in entry.get("anchors", []):                  # 2. ANCHOR
            if not anchor_exists(src, anchor):
                report.fail(rel, f"cites `{anchor}` in `{path}`, but no such "
                                 f"declaration is there any more")

        head = "\n".join(src.split("\n")[:BANNER_WINDOW_LINES])   # 4. BANNER
        if BANNER_MARKER not in head:
            report.fail(rel, f"`{path}` has no `Algorithm doc: {DOCS_DIR}/…` "
                             f"banner in its first {BANNER_WINDOW_LINES} lines — "
                             f"a reader who opens the file must be able to find "
                             f"this doc")

    for knob, value, path in parse_knob_rows(text):              # 3. KNOB
        # A knobs table names its file once per row, and a repo path repeated
        # down fifteen rows renders as an unreadable column. So a row may give
        # just the BASENAME and have it resolved against this doc's declared
        # sources — the full paths are already in the frontmatter.
        resolved = path if read(path) is not None else _resolve_basename(path, bound)
        src = read(resolved) if resolved else None
        if src is None:
            report.fail(rel, f"knob `{knob}` points at `{path}`, which is neither "
                             f"a file nor one of this doc's declared sources")
            continue
        path = resolved
        ok, actual = documented_value_matches(src, knob, value)
        if actual is None:
            report.fail(rel, f"knob `{knob}` is not assigned anywhere in `{path}`")
        elif not ok:
            report.fail(rel, f"knob `{knob}` is documented as `{value}` but "
                             f"`{path}` says `{actual}`")

    return bound


def check_coupling(base: str, bindings: dict[str, set[str]], report: Report) -> None:
    """5. COUPLING — did a bound source move without its dossier?"""
    try:
        changed = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...HEAD"],
            cwd=REPO, capture_output=True, text=True, check=True,
        ).stdout.split()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        report.warn("coupling", f"could not diff against `{base}` ({exc}); "
                                f"skipping the coupling check")
        return

    changed_set = set(changed)
    for doc, sources in bindings.items():
        touched = sorted(sources & changed_set)
        if touched and doc not in changed_set:
            report.warn(doc, "these bound sources changed but the doc did not — "
                             "re-read the pseudocode and confirm it still holds: "
                             + ", ".join(touched))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", help="git ref to diff against for the coupling "
                                   "check (e.g. origin/main)")
    ap.add_argument("--list-bindings", action="store_true",
                    help="print each dossier and the sources it binds, then exit")
    args = ap.parse_args()

    docs = load_docs()
    if not docs:
        print(f"no dossiers found under {DOCS_DIR}/", file=sys.stderr)
        return 1

    report = Report()
    bindings: dict[str, set[str]] = {}
    for rel, text, meta in docs:
        bindings[rel] = check_doc(rel, text, meta, report)

    if args.list_bindings:
        for doc, sources in bindings.items():
            print(doc)
            for path in sorted(sources):
                print(f"    {path}")
        return 0

    if args.base:
        check_coupling(args.base, bindings, report)

    for msg in report.warnings:
        print(f"WARN  {msg}")
    for msg in report.failures:
        print(f"FAIL  {msg}")

    n_sources = len({p for s in bindings.values() for p in s})
    if report.failures:
        print(f"\n{len(report.failures)} failure(s) across {len(docs)} dossiers.")
        print(f"Fix the doc (or the binding) in {DOCS_DIR}/ — see "
              f"{DOCS_DIR}/README.md for the contract.")
        return 1

    tail = f", {len(report.warnings)} warning(s)" if report.warnings else ""
    print(f"OK    {len(docs)} dossiers, {n_sources} bound source files{tail}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
