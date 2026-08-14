"""
The sticker line — what each sticker says, and what it votes for.

A sticker is one sentence and one QR code. The sentence is the provocation; the
code is the only way to act on it. Everything else about the design exists to
get someone from the first to the second.

Every line binds to a vote type that already exists on the `nyc-proposals` map,
so a scan lands on a real, countable proposal rather than inventing vocabulary
at the kerb. Two things follow from that binding and are enforced here:

  * `kind` mirrors the map's `pointType`. A "point" line can be cast the instant
    we know where the scanner is standing. A "route" line describes a corridor,
    which a single shared coordinate cannot express — the map opens with one end
    placed and asks for the other. Lines are written to match: a point line names
    a spot ("this corner"), a route line names a stretch ("this street").
  * `src` is the campaign tag that rides into the map-load beacon as `?src=`.
    It is per MESSAGE, not per sticker, on purpose — see codes.py for why the
    per-sticker identity lives in Postgres instead.

Lines are capped at MAX_LINE characters. That is not a rendering limit (the art
auto-fits whatever it is given); it is an editorial one. A sticker read from six
feet away, at an angle, in the rain, gets about four words.
"""

# The disc sets its line as large as the circle allows, so a long line is a
# small line. Past this the type drops under the legibility floor for a 2.5"
# round and the sticker stops working as a sticker.
MAX_LINE = 26

#: The longest single WORD, which is what actually decides how big a line sets.
#:
#: This replaced a three-word cap, and the replacement is the interesting part.
#: The word cap was standing in for "short enough to read at a glance", and it
#: misfires: a monospaced stack is sized by its longest LINE, a line cannot be
#: broken mid-word, so one long word sets the whole sticker. Measured on the
#: 2.5":
#:
#:     "WHAT SIDE ARE YOU ON?"    5 words, longest 4 chars  -> 16.5 pt
#:     "FIX THIS INTERSECTION."   3 words, longest 13 chars -> 14.6 pt
#:
#: Five short words read bigger than three long ones. Counting words was
#: rejecting the better sticker.
MAX_WORD_CHARS = 13


class Sticker:
    """One printed message: the words, the vote it casts, the tag it reports."""

    def __init__(self, key: str, line: str, vote_type: str, kind: str,
                 src: str, note: str):
        self.key = key
        self.line = line
        self.vote_type = vote_type
        self.kind = kind
        self.src = src
        self.note = note

    @property
    def display(self) -> str:
        """The line as it is set: mono capitals, the house voice."""
        return self.line.upper()


def _s(key, line, vote_type, kind, note):
    return Sticker(key, line, vote_type, kind, f"stk-{key}", note)


# --------------------------------------------------------------------------
# The line. The first three are the commissioned starters; the fourth extends
# the same voice — second person, present tense, no slogan, no brand. Each one
# is a complaint a passer-by has already had, printed where they had it.
# --------------------------------------------------------------------------

STICKERS = [
    # ── The three starters ────────────────────────────────────────────────
    _s("wait", "Tired of waiting?", "Fix signal timing", "point",
       "The signal-timing ask. 'Fix signal timing' is the map's existing label "
       "for a cycle that makes people wait too long to cross — see README for "
       "why that beat 'shorten light signal'."),
    _s("fix", "Fix this intersection.", "Add traffic calming", "route",
       "Commissioned mapping. 'Add traffic calming' is a ROUTE type, so this "
       "sticker opens the map with one end placed and asks for the other; see "
       "README 'The route-kind wrinkle'."),
    _s("whose", "Whose streets?", "Add tree", "point",
       "Reclaiming the roadbed, in its softest form: a tree in it. Needs the "
       "'Add tree' type added to nyc-proposals — see README."),
    _s("side", "What side are you on?", "Add crosswalk", "route",
       "Literally which side of the road you are stranded on, and audibly the "
       "other thing. Same double reading as 'Whose streets?'. Casts the "
       "crossing ask that left with 'No way across.', in better words."),


]

BY_KEY = {s.key: s for s in STICKERS}

# The starters, in the order they were commissioned — the default print run.
STARTERS = ["wait", "fix", "whose"]


def validate() -> list[str]:
    """Editorial invariants. Returned rather than raised so the CLI can print
    every problem at once instead of one per run."""
    problems = []
    seen_src = {}
    for s in STICKERS:
        longest = max(len(w) for w in s.display.split())
        if longest > MAX_WORD_CHARS:
            problems.append(
                f"{s.key}: longest word is {longest} chars, max "
                f"{MAX_WORD_CHARS} — it would set the whole line small "
                f"({s.line!r})"
            )
        if len(s.display) > MAX_LINE:
            problems.append(
                f"{s.key}: line is {len(s.display)} chars, max {MAX_LINE} "
                f"({s.display!r})"
            )
        if s.kind not in ("point", "route"):
            problems.append(f"{s.key}: kind must be point|route, got {s.kind!r}")
        if not s.line[0].isupper():
            problems.append(f"{s.key}: line should start with a capital")
        prior = seen_src.get(s.src)
        if prior:
            problems.append(f"{s.key}: src tag {s.src!r} already used by {prior}")
        seen_src[s.src] = s.key
        if len(s.src) > 32:
            problems.append(f"{s.key}: src tag {s.src!r} exceeds 32 chars")
    return problems
