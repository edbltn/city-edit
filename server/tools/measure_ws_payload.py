"""Measure the WebSocket vote payload, JSON vs binary frame.

Uses REAL data: edge ids come from actual casts in the local Postgres (which
carries prod-shaped nyc-bikes votes), and block ids come from the city's
edge->block mapping, so the id gaps — the thing the delta-varint encoding
lives or dies on — are the real ones, not synthetic.

    cd server && ./env/bin/python tools/measure_ws_payload.py
"""
import json
import os
import statistics
import sys
import zlib

import numpy as np
import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import wire_codec as w  # noqa: E402

MAP = "nyc-bikes"
CITY = "nyc"
VT_ID = 75107                      # a real vote_type_id — past the old 16-bit ceiling
VT_LABEL = "Protected bike lane"
EDGE_BLOCKS = f"osm_data/{CITY}/edge_blocks_streets.npy"


def json_delta(rev, edges, blocks, mode="bikepaths"):
    """Exactly what vote_store.publish_delta emits today."""
    return {
        "type": "delta", "rev": rev, "edges": list(edges), "m": mode,
        "vt": VT_ID, "dir": 1, "reversed": False, "vtLabel": VT_LABEL,
        "vtCounts": {str(e): [3, 0] for e in edges},
        "blockCounts": {str(b): [3, 0] for b in blocks},
    }


def binary_frame(rev, edges, blocks, mode=0):
    g = w.Group(rev, mode, VT_ID, VT_LABEL,
                {int(e): (3, 0) for e in edges},
                {int(b): (3, 0) for b in blocks})
    return w.encode_frame(w.Frame(w.KIND_DELTA, rev, rev - 1, [g]))


def deflate(b: bytes) -> int:
    """permessage-deflate is raw DEFLATE, not gzip."""
    c = zlib.compressobj(6, zlib.DEFLATED, -zlib.MAX_WBITS)
    return len(c.compress(b) + c.flush())


def row(label, js, bi):
    saved = 100 * (1 - bi / js) if js else 0
    print(f"  {label:<26} {js:>9,}  {bi:>9,}   {js / bi:>6.1f}x  {saved:>5.1f}%")


def main():
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    ebid = np.load(os.path.join(os.path.dirname(__file__), "..", EDGE_BLOCKS),
                   mmap_mode="r")

    # Real cast sizes: one row per (device, vote type) group on this map.
    cur.execute("""select count(*) n from edge_votes where map_slug=%s
                   group by device_id, vote_type_id order by n""", (MAP,))
    sizes = [r[0] for r in cur.fetchall()]
    print(f"{MAP}: {len(sizes)} casts, median {statistics.median(sizes):.0f} edges, "
          f"p90 {sizes[int(len(sizes) * 0.9)]}, max {max(sizes)}\n")

    def sample(n):
        cur.execute("""select device_id, vote_type_id from edge_votes
                       where map_slug=%s group by 1,2 having count(*)=%s limit 1""",
                    (MAP, n))
        got = cur.fetchone()
        if not got:
            return None
        cur.execute("""select edge_id from edge_votes where map_slug=%s
                       and device_id=%s and vote_type_id=%s order by edge_id""",
                    (MAP, got[0], got[1]))
        edges = [r[0] for r in cur.fetchall()]
        blocks = sorted({int(ebid[e]) for e in edges
                         if 0 <= e < len(ebid) and ebid[e] >= 0})
        return edges, blocks

    print("ONE VOTE (a single cast, as the WS sends it)")
    print(f"  {'':<26} {'JSON':>9}  {'binary':>9}   {'ratio':>7} {'saved':>6}")
    cases = []
    for n, name in ((1, "1 edge (a pin)"),
                    (int(statistics.median(sizes)), "median route"),
                    (sizes[int(len(sizes) * 0.9)], "p90 route"),
                    (max(sizes), "largest route")):
        s = sample(n)
        if not s:
            continue
        edges, blocks = s
        js = json.dumps(json_delta(41, edges, blocks)).encode()
        bi = binary_frame(41, edges, blocks)
        cases.append((name, len(edges), len(blocks), js, bi))
        row(f"{name} ({len(edges)}e/{len(blocks)}b)", len(js), len(bi))

    print("\n  same payloads under permessage-deflate, if it were negotiated")
    for name, ne, nb, js, bi in cases:
        row(f"{name} ({ne}e/{nb}b)", deflate(js), deflate(bi))

    # ── the sync packet ───────────────────────────────────────────────────
    # Today a foregrounding tab repairs itself with a full
    # /api/graph-votes?format=sparse refetch. The sync frame carries only the
    # cells that moved while the tab was hidden.
    print("\nSYNC (a tab returning after N votes landed on the map)")
    med = sample(int(statistics.median(sizes)))
    p90 = sample(sizes[int(len(sizes) * 0.9)])
    # Pessimistic: every vote lands on a DIFFERENT corridor, so nothing
    # coalesces away. Real traffic repeats corridors, and repeats collapse to
    # one cell at their final count.
    for n_votes in (1, 3, 10, 30):
        rev = 100
        merged_e, merged_b = {}, {}
        for i in range(n_votes):
            edges, blocks = (med if i % 2 == 0 else p90)
            merged_e.update({(e + i * 977) % 3_299_151: (3, 0) for e in edges})
            merged_b.update({(b + i * 131) % 287_351: (3, 0) for b in blocks})
            rev += 1
        frame = w.encode_frame(w.Frame(
            w.KIND_SYNC, rev, 100,
            [w.Group(rev, 0, VT_ID, VT_LABEL, merged_e, merged_b)]))
        print(f"  after {n_votes:>2} votes  ({len(merged_e):>5} edge cells, "
              f"{len(merged_b):>4} block cells):  sync frame "
              f"{len(frame):>7,} B   deflated {deflate(frame):>6,} B")

    print("\n  before — what a foreground repair costs today (full refetch):")
    import subprocess
    for slug, mode in ((MAP, "bikepaths"), ("nyc-walkways", "walkways")):
        try:
            out = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-H", "Accept-Encoding: gzip",
                 "-w", "%{size_download} %{http_code}",
                 f"https://cityedit.org/api/graph-votes?map={slug}"
                 f"&mode={mode}&format=sparse"],
                capture_output=True, text=True, timeout=120)
            size, code = out.stdout.split()
            print(f"    prod GET /api/graph-votes?map={slug}&format=sparse "
                  f"(gzip): {int(size):>10,} B  [{code}]")
        except Exception as e:
            print(f"    ({slug} measurement unavailable: {e})")


if __name__ == "__main__":
    main()
