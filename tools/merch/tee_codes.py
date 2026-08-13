"""
One code per shirt.

Every printed shirt that carries a QR gets its OWN code, for the same reason
every sticker does: the code is an identity, and the whole feature is that it
binds to one proposal the first time somebody votes from it. Two shirts sharing
a code would fight over the same binding, and the loser would spend its life
pointing at a stranger's crossing.

Derived, not drawn — sha1 over (campaign, print, index) — so a reprint of run 2
is the same ten codes. Minting more shirts means extending the index range,
never reshuffling it.

The campaign is deliberately NOT the sticker campaign. Codes share one namespace
and one table, so a distinct seed is what guarantees a tee can never mint the
same five characters as a lamp post.
"""

import hashlib

# Same 31-character alphabet as the stickers, and it has to be: the server
# validates every scanned code against it (database._STICKER_CODE), and the
# whole set is inside QR's alphanumeric mode.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_LEN = 5

BASE_URL = "HTTPS://CITYEDIT.ORG/S/"

CAMPAIGN = "tee-2026-08"


def mint(print_key: str, index: int, campaign: str = CAMPAIGN) -> str:
    """The code for the `index`-th copy of a given print. Pure and stable."""
    seed = f"{campaign}/{print_key}/{index}".encode()
    n = int.from_bytes(hashlib.sha1(seed).digest()[:8], "big")
    out = []
    for _ in range(CODE_LEN):
        n, r = divmod(n, len(ALPHABET))
        out.append(ALPHABET[r])
    return "".join(out)


def url_for(code: str) -> str:
    """What goes in the QR — upper-case, so it encodes in alphanumeric mode."""
    return BASE_URL + code.upper()


def mint_run(print_key: str, count: int, campaign: str = CAMPAIGN) -> list[str]:
    """`count` codes for one print.

    Raises on a collision rather than printing two shirts that bind to the same
    proposal. At 31^5 it is vanishingly unlikely — but "vanishingly unlikely"
    and "printed on a garment" are a bad pair.
    """
    codes = [mint(print_key, i, campaign) for i in range(count)]
    if len(set(codes)) != len(codes):
        dupes = sorted({c for c in codes if codes.count(c) > 1})
        raise ValueError(f"code collision minting {print_key}: {dupes}")
    return codes


def seed_sql(rows: list[dict], campaign: str = CAMPAIGN) -> str:
    """
    INSERT statements for the codes, so a scan is not a 404 at the kerb.

    ON CONFLICT DO NOTHING makes it safe to re-run: a code that has already been
    bound by a real scanner must never be reset by a reprint of the sheet it
    came from.
    """
    lines = [
        "-- City Edit tee codes. Safe to re-run: a bound code is never reset.",
        f"-- campaign: {campaign}",
        "",
    ]
    for r in rows:
        vals = ", ".join(
            "'" + str(r[k]).replace("'", "''") + "'"
            for k in ("code", "map_slug", "vote_type", "headline", "src_tag")
        )
        lines.append(
            "INSERT INTO sticker_codes (code, map_slug, vote_type, headline, "
            f"src_tag, campaign) VALUES ({vals}, '{campaign}') "
            "ON CONFLICT (code) DO NOTHING;"
        )
    return "\n".join(lines) + "\n"
