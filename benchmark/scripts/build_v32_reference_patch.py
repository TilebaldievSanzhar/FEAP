"""Prepare the reviewed pine hierarchy correction; never modify reference data."""
import hashlib
import json
from contextlib import closing
from pathlib import Path

import psycopg2

from feap.config import settings
from scripts.parse.ett_nomenclature import parse_group_pdf


def main():
    target = Path('data/reference_patches/2026-09-21-pine-hierarchy.json')
    if target.exists():
        raise FileExistsError('Existing review is immutable; use another version')
    root = Path('benchmark/data/reference/v32')
    source = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))['44']
    pdf = root / '44.pdf'
    if hashlib.sha256(pdf.read_bytes()).hexdigest() != source['sha256']:
        raise ValueError('Official source bytes changed')
    parsed = parse_group_pdf(pdf, '44')
    codes = sorted(c for c in parsed['leaves'] if c[:8] in {'44071134', '44071139', '44071194', '44071199'})
    if len(codes) != 16:
        raise ValueError('Expected exactly the 16 reviewed pine leaves')
    changes = []
    with closing(psycopg2.connect(settings.database_url_sync)) as connection, connection.cursor() as cursor:
        for code in codes:
            cursor.execute('SELECT name_full FROM tnved_tree WHERE code=%s AND is_leaf', (code,))
            before = cursor.fetchone()
            if before is None:
                raise ValueError(f'Missing leaf {code}')
            changes.append({'code': code, 'before': before[0],
                            'after': ' 🠺 '.join(parsed['leaves'][code]['full_name'].split(' > '))})
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({
        'reason': 'Restore all numbered and unnumbered tariff parents; distinguish planed pine from other sawn pine.',
        'source': {**source, 'pdf_pages_one_based': [10, 11, 12]},
        'scope': 'Only name_full of the 16 reviewed leaves; codes, tree edges and rates stay as stored.',
        'changes': changes,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print(target)


if __name__ == '__main__':
    main()
