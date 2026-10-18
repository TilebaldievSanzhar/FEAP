"""Collector: EAEU collection of preliminary classification decisions (registry ЕС 999).

«Сборник принятых предварительных решений таможенных органов государств – членов
ЕАЭС по классификации товаров» is published by the EEC in the EAEU reference-data
portal (nsi.eaeunion.org, registry code 1999, Решение КТС № 297). Records are
anonymised by the authorities: code, product description, country of the
authority, applied interpretation rules.

The portal is a single-page app; its public JSON API (no authentication):
    POST /portal/api/dictionaries/1999/get-list-data-total  {"date": ...}
    POST /portal/api/dictionaries/1999/get-list-data        {"date", "offset", "limit"}

Raw pages are stored unchanged with their hashes; build_tier_a turns them into
cases offline.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

API = 'https://nsi.eaeunion.org/portal/api/dictionaries/1999'
REGISTRY_ID = '25456099-2be3-4f1c-9d53-e608266a5abd'
TITLE = ('Сборник принятых предварительных решений таможенных органов государств – членов '
         'ЕАЭС по классификации товаров (ЕС 999)')
HEADERS = {
    'User-Agent': 'FEAP benchmark collector (research; contact via the EEC portal)',
    'Accept': 'application/json',
    'Content-Type': 'application/json',
}


async def _post(client: httpx.AsyncClient, path: str, body: dict, *, retries: int = 3) -> bytes:
    for attempt in range(retries + 1):
        try:
            response = await client.post(API + path, json=body)
            response.raise_for_status()
            return response.content
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            if attempt == retries:
                raise
            logger.warning('%s failed (%s), retry %d/%d', path, exc, attempt + 1, retries)
            await asyncio.sleep(5 * (attempt + 1))
    raise AssertionError('unreachable')


async def collect(out_dir: Path, *, on_date: dt.date, page_size: int = 1000,
                  delay_s: float = 2.0) -> dict:
    """Download every page as of on_date into out_dir; return the manifest."""
    out_dir.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(headers=HEADERS, timeout=180) as client:
        total = json.loads(await _post(client, '/get-list-data-total', {'date': on_date.isoformat()}))
        expected = total['totalCount']
        pages = []
        for offset in range(0, expected, page_size):
            body = {'date': on_date.isoformat(), 'offset': offset, 'limit': page_size}
            raw = await _post(client, '/get-list-data', body)
            path = out_dir / f'page_{offset:06d}.json'
            path.write_bytes(raw)
            n = len(json.loads(raw))
            pages.append({'file': path.name, 'offset': offset, 'records': n,
                          'sha256': hashlib.sha256(raw).hexdigest()})
            logger.info('offset %d: %d records', offset, n)
            await asyncio.sleep(delay_s)
    manifest = {
        'source': TITLE, 'api': API, 'registry_id': REGISTRY_ID,
        'on_date': on_date.isoformat(),
        'fetched_at': dt.datetime.now(dt.UTC).isoformat(),
        'total_count': expected, 'update_date': total.get('updateDate'),
        'records': sum(p['records'] for p in pages), 'pages': pages,
    }
    if manifest['records'] != expected:
        logger.error('Got %d records, registry reports %d', manifest['records'], expected)
    (out_dir / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest


def load_records(raw_dir: Path) -> tuple[list[dict], dict]:
    """Records of a downloaded snapshot, with the snapshot manifest; ids must be unique."""
    manifest = json.loads((raw_dir / 'manifest.json').read_text(encoding='utf-8'))
    records: list[dict] = []
    for page in manifest['pages']:
        raw = (raw_dir / page['file']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != page['sha256']:
            raise ValueError(f"{page['file']} changed since download")
        records.extend(json.loads(raw))
    ids = [r['id'] for r in records]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate record ids across pages: the registry moved during download')
    return records, manifest
