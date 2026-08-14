#!/usr/bin/env python3
"""Make the audience features visible on local dev, where they are correctly invisible.

Both counts dedupe on the COUNTING identity (server/vote_identity.py) — the
hashed client IP — so on localhost every browser tab, every reload and every
incognito window is ONE person. That is the feature working: it is exactly what
stops a fragmenting device_id from inventing an audience. It also means you
cannot see either number on your own machine, because:

  · co-presence renders only from 3 distinct people (you + two others), and
  · a view count renders only from 2.

This script supplies the other people. It does NOT bypass the identity rule —
it satisfies it, by opening real WebSocket connections that arrive with
distinct `X-Forwarded-For` headers, which is what `get_client_ip()` reads. What
you see in the browser is the real code path, with real Redis state.

    python tools/dev_audience.py                       # 3 viewers on nyc-walkways
    python tools/dev_audience.py --map e-bikes-3 -n 5
    python tools/dev_audience.py --no-seed-views       # co-presence only

By default it also watches Redis for view sketches as you create them: open a
proposal card in the browser, hold it for the two-second dwell, and the blocks
that proposal spans appear as `pv:<slug>:<type>:<block>` keys. The script adds
the same synthetic people to each one, so reopening the card shows a real
count — computed by the real union read, not injected into the UI.

Ctrl-C to stop; the viewers leave the room immediately.
"""

import argparse
import hashlib
import json
import sys
import threading
import time

try:
    from websocket import create_connection  # websocket-client
except ImportError:
    sys.exit("pip install websocket-client  (or: uv pip install websocket-client)")

try:
    import redis
except ImportError:
    sys.exit("redis-py is missing — run this from server/env")


# Documentation-range addresses (RFC 5737): never routable, obviously fake in a
# log, and distinct enough that each hashes to its own counting identity.
FAKE_IPS = [f"203.0.113.{10 + i}" for i in range(64)]


def synthetic_identity(ip: str) -> str:
    """What the server will store for this connection — get_client_ip()'s hash.

    Mirrored here so the seeded VIEW members are the same people as the
    presence members, rather than a second, invented population.
    """
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


def hold_viewers(ws_url: str, slug: str, n: int) -> list:
    conns = []
    for i in range(n):
        ip = FAKE_IPS[i % len(FAKE_IPS)]
        ws = create_connection(f"{ws_url}?map={slug}&bin=1", timeout=20,
                               header={"X-Forwarded-For": ip})
        conns.append(ws)
        # Each socket needs something draining it, or the server's queue backs
        # up and the hub eventually drops the client as overflowed.
        threading.Thread(target=_drain, args=(ws,), daemon=True).start()
    return conns


def _drain(ws) -> None:
    while True:
        try:
            ws.recv()
        except Exception:
            return


def seed_views(r, slug: str, identities: list, stop: threading.Event) -> None:
    """Add the same synthetic people to every view sketch the browser creates."""
    seeded: set[str] = set()
    while not stop.is_set():
        fresh = 0
        try:
            for key in r.scan_iter(match=f"pv:{slug}:*", count=500):
                if key in seeded:
                    continue
                seeded.add(key)
                r.pfadd(key, *identities)
                fresh += 1
        except Exception as e:
            print(f"  [seed] {e}", flush=True)
        # Only on CHANGE, and one line per event: this usually runs with its
        # output redirected to a file, where a \r-updated progress line becomes
        # thousands of concatenated copies of itself.
        if fresh:
            print(f"  +{fresh} block sketches seeded ({len(seeded)} total) "
                  f"— close and reopen the card to see the count", flush=True)
        stop.wait(1.0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--map", default="nyc-walkways", help="map slug (default: nyc-walkways)")
    ap.add_argument("-n", "--viewers", type=int, default=3,
                    help="how many other people to simulate (default: 3)")
    ap.add_argument("--ws", default="ws://localhost:5001/ws", help="WebSocket URL")
    ap.add_argument("--redis-host", default="localhost")
    ap.add_argument("--redis-port", type=int, default=6379)
    ap.add_argument("--no-seed-views", action="store_true",
                    help="co-presence only; leave view counts alone")
    args = ap.parse_args()

    r = redis.Redis(host=args.redis_host, port=args.redis_port, db=0,
                    decode_responses=True)
    r.ping()

    print(f"Opening {args.viewers} simulated viewers on '{args.map}' …")
    conns = hold_viewers(args.ws, args.map, args.viewers)
    time.sleep(2.5)
    live = r.zcard(f"pres:{args.map}")
    others = max(0, live - 1) if live > args.viewers else args.viewers
    print(f"  {live} distinct people present (Redis pres:{args.map}) — "
          f"{args.viewers} of them simulated")
    print(f"\nOpen http://localhost:3000/m/{args.map} — the strip should read "
          f"\"…with {others} other people\".")
    print("It counts everyone present EXCEPT you, so your own tab never adds to "
          "the number it shows you.")
    print("If nothing appears, check the tab is genuinely FOREGROUND: a hidden "
          "or occluded tab leaves the room on purpose.")

    stop = threading.Event()
    if not args.no_seed_views:
        ids = [synthetic_identity(FAKE_IPS[i % len(FAKE_IPS)])
               for i in range(args.viewers)]
        print(f"\nWatching for view sketches. Open a proposal card, hold it ~2s, "
              f"then close and reopen it.")
        threading.Thread(target=seed_views, args=(r, args.map, ids, stop),
                         daemon=True).start()

    print("\nCtrl-C to stop.\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        for ws in conns:
            try:
                ws.close()
            except Exception:
                pass
        time.sleep(0.5)
        print(f"\nStopped. {r.zcard(f'pres:{args.map}')} people still present.")


if __name__ == "__main__":
    main()
