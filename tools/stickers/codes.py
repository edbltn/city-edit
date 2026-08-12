"""
Sticker codes — the identity every printed disc carries.

Each physical sticker gets its OWN code, because each one ends up on a
different pole. The code is what turns an anonymous scan into a located one:
the first person to scan it shares their location and votes, and from then on
the code IS that location, so everyone after them skips the prompt.

## The URL

    HTTPS://CITYEDIT.ORG/S/AB3KM

Uppercase, and that is not a stylistic choice. QR has an "alphanumeric" mode
that packs uppercase, digits and a handful of symbols at 5.5 bits per character
instead of byte mode's 8. The whole URL is inside that character set, so the
28-character link fits a version-3 code (29 modules) at error-correction level
H — the highest, 30% of the code can be destroyed and it still decodes. The
same URL in lowercase falls into byte mode and needs version 4 (33 modules),
which is 27% more modules crammed into the same 1.3 inches of sticker. The
server lowercases the path, so the shouting never reaches the user.

## Why the code is not the UTM tag

Every sticker reports two different things, at two different resolutions:

  * `?src=stk-wait` — the MESSAGE. This rides into the map-load beacon and
    becomes the `src` label on the `cityedit_map_load_ms` metric, which is a
    Cloud Monitoring log-based metric. Metric labels are a cardinality budget:
    a dozen message tags is a dashboard, ten thousand per-sticker tags is a
    billing incident and an unreadable chart.
  * the code itself — the STICKER. Scans, first-vote, and the location it
    resolved to are columns on `sticker_codes` in Postgres, where high
    cardinality is just rows. Per-sticker questions ("which poles actually get
    scanned?") are SQL, not Metrics Explorer.

So: the dashboard groups by message, the database answers per sticker.

## Determinism

Codes are derived, not drawn: sha1 over (campaign, message key, index). Re-run
the build and you get the same codes, so a reprint of sheet 3 is the same
thirty-six stickers — critical once some of them are already on poles and
resolved to a location. Minting new stickers means extending the index range,
never reshuffling it.
"""

import hashlib

# No 0/O/1/I/L — a code gets read aloud, typed in, and photographed off a pole.
# All 32 characters are inside QR's alphanumeric set.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_LEN = 5

BASE_URL = "HTTPS://CITYEDIT.ORG/S/"

# Bumping this re-mints every code. It exists so a future print run can be made
# deliberately distinct from an earlier one that is already in the field.
CAMPAIGN = "nyc-2026-08"


def mint(message_key: str, index: int, stock: str = "2.5",
         campaign: str = CAMPAIGN) -> str:
    """The code for the `index`-th copy of a message on a given stock. Pure
    function of its inputs — same arguments, same code, forever.

    `stock` is in the seed because the 2.5" and the 3" are different physical
    stickers that go on different poles. Leaving it out made both sheets mint
    the same twelve codes, so a 3" sticker would silently inherit whatever
    location its 2.5" twin had already resolved to.
    """
    seed = f"{campaign}/{stock}/{message_key}/{index}".encode()
    digest = hashlib.sha1(seed).digest()
    # Fold the digest into base-31 rather than slicing bytes, so every character
    # draws on the whole hash instead of one byte apiece.
    n = int.from_bytes(digest[:8], "big")
    out = []
    for _ in range(CODE_LEN):
        n, r = divmod(n, len(ALPHABET))
        out.append(ALPHABET[r])
    return "".join(out)


def url_for(code: str) -> str:
    """The string that goes into the QR — uppercase, see the module docstring."""
    return BASE_URL + code.upper()


def mint_run(message_key: str, count: int, stock: str = "2.5", start: int = 0,
             campaign: str = CAMPAIGN) -> list[str]:
    """`count` codes for one message on one stock, starting at index `start`.

    Raises on an internal collision rather than printing two stickers that
    resolve to the same pole. At 31^5 (28.6M) codes a collision inside one run
    is vanishingly unlikely, but "vanishingly unlikely" and "printed on paper"
    are a bad combination.
    """
    codes = [mint(message_key, start + i, stock, campaign) for i in range(count)]
    if len(set(codes)) != len(codes):
        dupes = sorted({c for c in codes if codes.count(c) > 1})
        raise ValueError(f"code collision minting {message_key}: {dupes}")
    return codes
