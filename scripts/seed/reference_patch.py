"""Apply a reviewed tnved_tree names patch atomically, rejecting stale reference rows.

Usage:
  python -m scripts.seed.reference_patch PATH [--apply] [--reverse]
      Without --apply, validates current rows and prints the number of planned changes.
  python -m scripts.seed.reference_patch --from-tree data/parsed/tree.json --write PATH
      Writes the patch that brings the database names to a rebuilt tree.json.

A change is ``{"code", "before", "after"}`` plus an optional ``"field"``:
``name_full`` (default — the earlier leaf-only patches) or ``name``. Codes are
groups, headings, subheadings or leaves; sections are never patched. Every row
is checked before any update, and ``--reverse`` checks the reverse preconditions.
"""
import argparse
import gzip
import json
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

from feap.config import settings

FIELDS = ("name", "name_full")


def _changes(patch):
    rows = patch["changes"]
    keys = [(r["code"], r.get("field", "name_full")) for r in rows]
    if not rows or len(keys) != len(set(keys)):
        raise ValueError("Patch must have distinct (code, field) changes")
    for code, field in keys:
        if field not in FIELDS:
            raise ValueError(f"Unsupported field: {field}")
        if len(code) not in (2, 4, 6, 10) or not code.isascii() or not code.isdigit():
            raise ValueError("Expected a group, heading, subheading or ten-digit leaf code")
    return rows, keys


def apply_patch(connection, patch, *, apply=False, reverse=False):
    rows, keys = _changes(patch)
    before, after = ("after", "before") if reverse else ("before", "after")
    by_field = {f: [r for r, (_, k) in zip(rows, keys) if k == f] for f in FIELDS}
    with connection, connection.cursor() as cursor:
        for field, batch in by_field.items():
            if not batch:
                continue
            # field is one of FIELDS, never user text
            cursor.execute(f"SELECT code,{field} FROM tnved_tree WHERE code = ANY(%s) FOR UPDATE",
                           ([r["code"] for r in batch],))
            current = dict(cursor.fetchall())
            for row in batch:
                if row["code"] not in current or current[row["code"]] != row[before]:
                    raise ValueError(f"Stale or missing reference row: {row['code']} {field}")
                if not row[after].strip():
                    raise ValueError("Empty replacement description")
        if apply:
            for field, batch in by_field.items():
                if batch:
                    cursor.executemany(f"UPDATE tnved_tree SET {field}=%s WHERE code=%s",
                                       [(r[after], r["code"]) for r in batch])
    return len(rows)


def diff_tree(connection, nodes):
    """Name changes from the database to a rebuilt tree; the code tree must be the same."""
    with connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT t.code, t.level, p.code, t.name, t.name_full FROM tnved_tree t "
            "LEFT JOIN tnved_tree p ON p.id = t.parent_id"
        )
        stored = {code: (level, parent, name, full) for code, level, parent, name, full in cursor.fetchall()}
    by_id = {n["id"]: n for n in nodes}
    tree = {n["code"]: n for n in nodes}
    if set(tree) != set(stored):
        raise ValueError(f"Code sets differ: {len(set(tree) ^ set(stored))} codes; reseed instead")
    changes = []
    for code, n in tree.items():
        level, parent, name, full = stored[code]
        tree_parent = by_id[n["parent_id"]]["code"] if n["parent_id"] else None
        if (level, parent) != (n["level"], tree_parent):
            raise ValueError(f"Tree structure differs at {code}; reseed instead")
        if n["level"] == 0:
            continue
        for field, value in (("name", name), ("name_full", full)):
            if n[field] != value:
                changes.append({"code": code, "field": field, "before": value, "after": n[field]})
    return changes


def load_patch(path: Path) -> dict:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def write_patch(path: Path, patch: dict) -> None:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "wt", encoding="utf-8") as f:
        json.dump(patch, f, ensure_ascii=False, indent=1)


def _connect():
    return closing(psycopg2.connect(settings.database_url_sync.replace("+psycopg2", "")))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path, nargs="?")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--reverse", action="store_true")
    parser.add_argument("--from-tree", type=Path, help="rebuilt tree.json to diff against the database")
    parser.add_argument("--write", type=Path, help="where to write the patch built with --from-tree")
    parser.add_argument("--reason", default="Names rebuilt from the official EEC nomenclature PDFs")
    args = parser.parse_args()

    if args.from_tree:
        if not args.write:
            parser.error("--from-tree needs --write")
        if args.write.exists():
            raise FileExistsError("Existing patch is immutable; use another name")
        nodes = json.loads(args.from_tree.read_text(encoding="utf-8"))
        nomenclature = args.from_tree.with_name("nomenclature.json")
        meta = json.loads(nomenclature.read_text(encoding="utf-8"))["meta"] if nomenclature.exists() else {}
        with _connect() as connection:
            changes = diff_tree(connection, nodes)
        write_patch(args.write, {
            "reason": args.reason,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": {k: meta[k] for k in ("source", "parser", "parsed_at", "files") if k in meta},
            "scope": "name and name_full of groups, headings, subheadings and leaves; codes and edges unchanged.",
            "changes": changes,
        })
        print(f"Wrote {len(changes)} changes to {args.write}")
        return

    if not args.path:
        parser.error("PATH is required")
    patch = load_patch(args.path)
    with _connect() as connection:
        count = apply_patch(connection, patch, apply=args.apply, reverse=args.reverse)
    print(f'{"Applied" if args.apply else "Validated"} {count} reference changes')


if __name__ == "__main__":
    main()
