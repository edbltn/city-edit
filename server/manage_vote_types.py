#!/usr/bin/env python3
"""
Admin CLI for a map's vote-type list.

A map's authored vote types live in `maps.custom_vote_types` (a JSONB array of
{label, icon, pointType}). There is no HTTP endpoint that edits them — a map's
vocabulary is what its heatmap MEANS, and changing it silently reshapes every
proposal on it — so changes go through this script, deliberately, against a
named database.

Usage (run with the target DATABASE_URL in the environment / server/.env):

    python manage_vote_types.py list nyc-proposals
    python manage_vote_types.py add nyc-proposals "Add tree" --icon trees --kind point
    python manage_vote_types.py remove nyc-proposals "Add tree"

`add` is idempotent by label: re-adding an existing label updates its icon/kind
rather than duplicating it, so this is safe to re-run from a runbook.

Two things do NOT happen here, on purpose:

  * Running Flask instances cache map configs in-process for 30–60s, so the new
    type appears within a minute rather than instantly. There is nothing to
    invalidate; just wait.
  * `remove` only takes the label out of the picker. Votes already cast under it
    stay exactly where they are and keep rendering — deleting the vocabulary
    entry must never delete anybody's vote. Use the DB directly if you really
    mean to destroy vote data.

Against prod, run it through the bastion tunnel (Postgres on local :5433 — see
docs/gcp-deployment.md), passing DATABASE_URL inline for the one command. Never
repoint server/.env.
"""
import argparse
import json
import sys

from dotenv import load_dotenv

load_dotenv()

from database import DATABASE_URL, get_cursor, normalize_point_type  # noqa: E402

VALID_KINDS = ("point", "route")


def _require_db() -> None:
    if not DATABASE_URL:
        sys.exit("DATABASE_URL is not set — point it at the target database first.")


def _load(slug: str) -> list[dict]:
    """The map's authored types. Refuses maps that inherit a shared preset list:
    editing those in place would silently rewrite every other map using them."""
    with get_cursor() as cursor:
        cursor.execute(
            "SELECT custom_vote_types, vote_type_list_id FROM maps WHERE slug = %s",
            (slug,),
        )
        row = cursor.fetchone()
    if not row:
        sys.exit(f"No map with slug '{slug}'")
    custom, list_id = row
    if custom is None and list_id is not None:
        sys.exit(
            f"'{slug}' inherits shared vote-type list #{list_id}. Editing it here "
            "would change every map that uses it — give this map its own "
            "custom_vote_types first."
        )
    return list(custom or [])


def _save(slug: str, types: list[dict]) -> None:
    with get_cursor() as cursor:
        cursor.execute(
            "UPDATE maps SET custom_vote_types = %s WHERE slug = %s",
            (json.dumps(types), slug),
        )


def cmd_list(args: argparse.Namespace) -> None:
    _require_db()
    types = _load(args.slug)
    if not types:
        print("(no custom vote types)")
        return
    width = max(len(t.get("label", "")) for t in types)
    for t in types:
        print(f"  {t.get('label', ''):{width}}  {t.get('pointType', '?'):5}  "
              f"{t.get('icon', '')}")
    print(f"\n{len(types)} vote type(s) on '{args.slug}'")


def cmd_add(args: argparse.Namespace) -> None:
    _require_db()
    kind = normalize_point_type(args.kind)
    if kind not in VALID_KINDS:
        sys.exit(f"--kind must be one of {', '.join(VALID_KINDS)}")
    types = _load(args.slug)
    entry = {"label": args.label, "icon": args.icon, "pointType": kind}

    for i, t in enumerate(types):
        if t.get("label") == args.label:
            if t == entry:
                print(f"= '{args.label}' already on '{args.slug}', unchanged")
                return
            types[i] = entry
            _save(args.slug, types)
            print(f"✓ updated '{args.label}' on '{args.slug}' → {kind}, {args.icon}")
            return

    types.append(entry)
    _save(args.slug, types)
    print(f"✓ added '{args.label}' to '{args.slug}' → {kind}, {args.icon} "
          f"({len(types)} types)")
    print("  Running Flask instances will pick it up within ~a minute.")


def cmd_remove(args: argparse.Namespace) -> None:
    _require_db()
    types = _load(args.slug)
    kept = [t for t in types if t.get("label") != args.label]
    if len(kept) == len(types):
        sys.exit(f"'{args.label}' is not on '{args.slug}'")
    _save(args.slug, kept)
    print(f"✓ removed '{args.label}' from '{args.slug}' ({len(kept)} types left)")
    print("  Votes already cast under that label are untouched and still render.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Edit a map's authored vote types.",
        epilog="Vote data is never modified — see the module docstring.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="Show a map's vote types")
    p_list.add_argument("slug")
    p_list.set_defaults(func=cmd_list)

    p_add = sub.add_parser("add", help="Add or update a vote type (idempotent)")
    p_add.add_argument("slug")
    p_add.add_argument("label")
    p_add.add_argument("--icon", required=True,
                       help="icon key from client-react/public/icons/")
    p_add.add_argument("--kind", required=True, choices=VALID_KINDS,
                       help="point (a spot) or route (a corridor)")
    p_add.set_defaults(func=cmd_add)

    p_rm = sub.add_parser("remove", help="Remove a vote type from the picker")
    p_rm.add_argument("slug")
    p_rm.add_argument("label")
    p_rm.set_defaults(func=cmd_remove)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
