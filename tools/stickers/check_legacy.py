#!/usr/bin/env python3
"""
Check that every sticker ALREADY IN THE WORLD still works.

    DATABASE_URL=… ./env/bin/python check_legacy.py
    DATABASE_URL=… ./env/bin/python check_legacy.py --base https://cityedit.org

`verify_prod.py` checks the run you are about to print. This checks the ones you
printed already — including the lines that have since been dropped from
`campaign.py`.

## Why this is a separate check

A retired sticker is not a deleted sticker. It is on a pole, and its code still
resolves, because the server answers a scan from the `sticker_codes` ROW — which
carries its own headline, map and vote type — and never reads `campaign.py`.
That separation is what makes it safe to change the printable set, and it is
also what makes the failure silent: drop a line and nothing in the build will
ever mention its codes again, so nothing in the build can notice if the map
stops offering the vote type they cast.

Which is the failure that matters. A vote type disappearing from the map does
not 404. `resolveEffectiveVoteType` falls back, so the sticker keeps scanning
and quietly casts something the person never asked for.

So this asks the DATABASE what is out there — every seeded code, retired or not
— and checks each one against the live map. It needs a `DATABASE_URL` because
the public API has no "list every code" endpoint, and should not have one.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).parent))

import campaign  # noqa: E402

UA = {"User-Agent": "cityedit-sticker-legacy-check"}


def live_vote_types(base: str, slug: str) -> set[str] | None:
    try:
        req = urllib.request.Request(f"{base}/api/maps/{slug}", headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            return {v["label"] for v in json.loads(r.read())["voteTypes"]}
    except Exception as e:
        print(f"could not read {slug}: {e}", file=sys.stderr)
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://cityedit.org")
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = ap.parse_args()
    if not args.database_url:
        print("set DATABASE_URL (or pass --database-url)", file=sys.stderr)
        return 1
    base = args.base.rstrip("/")

    with psycopg2.connect(args.database_url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT map_slug, vote_type, headline, count(*), "
            "       count(resolved_at), coalesce(sum(scans), 0) "
            "FROM sticker_codes GROUP BY 1, 2, 3 ORDER BY 3"
        )
        groups = cur.fetchall()

    if not groups:
        print("no sticker codes seeded")
        return 0

    printable = {(s.vote_type, s.display) for s in campaign.STICKERS}
    by_map: dict[str, set[str]] = defaultdict(set)
    for slug, vote_type, *_ in groups:
        by_map[slug].add(vote_type)

    live = {slug: live_vote_types(base, slug) for slug in by_map}
    if any(v is None for v in live.values()):
        return 1

    total = sum(g[3] for g in groups)
    print(f"{total} codes seeded across {len(by_map)} map(s), against {base}\n")
    print(f"  {'headline':26s} {'vote type':30s} {'n':>4s} {'bound':>6s} "
          f"{'scans':>6s}  status")

    broken = []
    for slug, vote_type, headline, n, bound, scans in groups:
        in_print = (vote_type, headline) in printable
        ok = vote_type in live[slug]
        if not ok:
            broken.append((headline, vote_type, n))
        status = ("ok" if ok else "MISSING FROM MAP") + \
                 ("" if in_print else "  (retired — legacy only)")
        print(f"  {headline[:26]:26s} {vote_type[:30]:30s} {n:>4d} {bound:>6d} "
              f"{scans:>6d}  {status}")

    print()
    if broken:
        print("These codes are on poles and would cast the WRONG proposal:",
              file=sys.stderr)
        for headline, vote_type, n in broken:
            print(f"  {n} x {headline!r} wants {vote_type!r}, which "
                  f"{by_map and 'the map no longer offers'}", file=sys.stderr)
        print("\nRe-add the type with server/manage_vote_types.py rather than "
              "leaving them to fall back silently.", file=sys.stderr)
        return 1

    retired = sum(n for _, vt, hl, n, _, _ in groups if (vt, hl) not in printable)
    print(f"every seeded code's vote type still exists on its map "
          f"({retired} of them from retired lines, still scannable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
