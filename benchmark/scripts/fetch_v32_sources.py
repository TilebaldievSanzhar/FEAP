"""Archive official EEC source bytes locally and publish reproducible hashes."""
import hashlib
import json
from pathlib import Path

import fitz
import httpx

SOURCES = {
    '16': 'https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.16_2022_10.09.2023.pdf',
    '44': 'https://eec.eaeunion.org/upload/files/catr/ett/ru.44_2022_16.06.2022.pdf',
    '84': 'https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.84_2022_27.04.2026.pdf',
    '85': 'https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.85_2022_05.07.2026.pdf',
    '87': 'https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.87_2022_22.01.2026.pdf',
    '94': 'https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.94_2022_08.03.2026.pdf',
    '95': 'https://eec.eaeunion.org/upload/files/catr/ett/ru.95_2022.pdf',
    '04': 'https://eec.eaeunion.org/upload/files/catr/ett/ru.04_2022_25.04.2022.pdf',
    '10': 'https://eec.eaeunion.org/upload/files/catr/ett/ru.10_2022_25.04.2022.pdf',
    '41': 'https://eec.eaeunion.org/upload/files/catr/ett/ru.41_2022.pdf',
}


def main():
    root = Path('benchmark/data/reference/v32')
    root.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for group, url in SOURCES.items():
        path = root / (group + '.pdf')
        if not path.exists():
            response = httpx.get(url, follow_redirects=True, timeout=60)
            response.raise_for_status()
            if not response.content.startswith(b'%PDF'):
                raise ValueError('Expected a PDF')
            path.write_bytes(response.content)
        with fitz.open(path) as document:
            pages = [page.get_text() for page in document]
        (root / (group + '.json')).write_text(json.dumps(pages, ensure_ascii=False), encoding='utf-8')
        manifest[group] = {'url': url, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                           'pages': len(pages), 'retrieved_on': '2026-09-21'}
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
