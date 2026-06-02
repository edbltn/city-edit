#!/usr/bin/env python3
"""
Admin CLI for map ⇄ subdomain wiring.

Vanity subdomains (e.g. bikepaths.cityedit.org → the nyc-bikes map) are just a
column on the `maps` row — the client resolves them at runtime via
/api/maps/by-subdomain/<sub>, so attaching one needs no code change or redeploy.

Usage (run with the target DATABASE_URL in the environment / server/.env):

    python manage_maps.py list
    python manage_maps.py set <slug> <subdomain>
    python manage_maps.py clear <slug>

Examples:
    python manage_maps.py set ny-bike-test bikes
    python manage_maps.py clear ny-bike-test

After `set`, point DNS for <subdomain>.<root> at this service and make sure TLS
covers it (see docs/url-routing.md) — then the subdomain serves that map and
apex/slug visitors redirect to it.
"""
import argparse
import sys

from dotenv import load_dotenv

load_dotenv()

from database import DATABASE_URL, list_maps, get_map, set_map_subdomain


def _require_db() -> None:
    if not DATABASE_URL:
        sys.exit("DATABASE_URL is not set — point it at the target database first.")


def cmd_list(_args: argparse.Namespace) -> None:
    _require_db()
    maps = list_maps()
    if not maps:
        print("(no maps)")
        return
    width = max(len(m["slug"]) for m in maps)
    for m in sorted(maps, key=lambda m: m["slug"]):
        sub = m.get("subdomain") or "—"
        print(f"{m['slug']:<{width}}  {sub:<14}  {m.get('name', '')}")


def cmd_set(args: argparse.Namespace) -> None:
    _require_db()
    if not get_map(args.slug):
        sys.exit(f"No map with slug '{args.slug}'.")
    ok, msg = set_map_subdomain(args.slug, args.subdomain)
    print(f"{'✓' if ok else '✗'} {args.slug}: {msg}")
    sys.exit(0 if ok else 1)


def cmd_clear(args: argparse.Namespace) -> None:
    _require_db()
    ok, msg = set_map_subdomain(args.slug, None)
    print(f"{'✓' if ok else '✗'} {args.slug}: {msg}")
    sys.exit(0 if ok else 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Map ⇄ subdomain admin.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List maps and their subdomains").set_defaults(func=cmd_list)

    p_set = sub.add_parser("set", help="Attach a subdomain to a map")
    p_set.add_argument("slug")
    p_set.add_argument("subdomain")
    p_set.set_defaults(func=cmd_set)

    p_clear = sub.add_parser("clear", help="Remove a map's subdomain")
    p_clear.add_argument("slug")
    p_clear.set_defaults(func=cmd_clear)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
