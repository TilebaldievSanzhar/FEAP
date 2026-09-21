"""Apply a reviewed name_full patch atomically, rejecting stale reference rows.

Usage: python -m scripts.seed.reference_patch PATH [--apply] [--reverse]
Without --apply, validates current rows and prints the number of planned changes.
"""
import argparse
import json
from contextlib import closing
from pathlib import Path

import psycopg2

from feap.config import settings


def apply_patch(connection, patch, *, apply=False, reverse=False):
    rows = patch['changes']
    codes = [r['code'] for r in rows]
    if not codes or len(codes) != len(set(codes)):
        raise ValueError('Patch must have distinct codes')
    if any(len(c) != 10 or not c.isascii() or not c.isdigit() for c in codes):
        raise ValueError('Expected ten-digit leaf codes')
    before, after = ('after', 'before') if reverse else ('before', 'after')
    with connection, connection.cursor() as cursor:
        cursor.execute('SELECT code,name_full FROM tnved_tree WHERE code = ANY(%s) AND is_leaf FOR UPDATE', (codes,))
        current = dict(cursor.fetchall())
        for row in rows:
            if row['code'] not in current or current[row['code']] != row[before]:
                raise ValueError(f"Stale or missing reference row: {row['code']}")
            if not row[after].strip():
                raise ValueError('Empty replacement description')
        if apply:
            cursor.executemany('UPDATE tnved_tree SET name_full=%s WHERE code=%s AND is_leaf',
                               [(r[after], r['code']) for r in rows])
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--reverse', action='store_true')
    args = parser.parse_args()
    patch = json.loads(args.path.read_text(encoding='utf-8'))
    with closing(psycopg2.connect(settings.database_url_sync)) as connection:
        count = apply_patch(connection, patch, apply=args.apply, reverse=args.reverse)
    print(f'{"Applied" if args.apply else "Validated"} {count} reference changes')


if __name__ == '__main__':
    main()
