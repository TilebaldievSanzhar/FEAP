"""Collector: EEC Collegium decisions on the classification of particular goods.

The EEC keeps the list on its site (code, decision number and date, product,
link to the text). A decision is binding in every member state; it names the
product in detail and the code — sometimes only to the heading:

    1. Тепловизионный прицел, представляющий собой …, в соответствии с Основными
       правилами интерпретации … 1 и 6 классифицируется в субпозиции 9013 10 000 0 …

The list page mixes UTF-8 and cp1251 rows; decision texts come as DOCX (read
with zipfile) or PDF (pdfplumber). Rows marked «внесены изменения» or
«отменено» are recorded with that status and left out by the builder.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import re
import zipfile
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

LIST_URL = ('https://eec.eaeunion.org/comission/department/dep_tamoj_zak/'
            'klassifikatsiya-tovarov-v-sootvetstvii-s-tn-ved-eaes/resheniya-o-klassifikatsii-tovarov.php')
HEADERS = {'User-Agent': 'FEAP benchmark collector (research)'}
UCHET = 'https://zakon.uchet.kz'

#: The basis may name notes too: «… правилом интерпретации … 1, примечанием 2 (а) к разделу
#: XVI … классифицируется в товарной позиции 8412». Rules are parsed from it later.
_ITEM = re.compile(
    r'^(?P<n>\d{1,2})\.\s*(?P<desc>.+?),?\s*в\s+соответствии\s+с\s+'
    r'(?:Основн\w+\s+правил\w+\s+интерпретации|ОПИ)(?P<rules>.*?)\s*'
    r'классифицир\w+\s+в\s+(?P<level>товарной\s+позиции|подсубпозиции|субпозиции)\s+'
    r'(?P<code>\d[\d\s]*\d)',
    re.IGNORECASE | re.DOTALL,
)


#: Older wording puts the code first: «1. В соответствии с ОПИ 1, 3(б) и 6 в подсубпозиции
#: 8474 20 000 3 … классифицируется дробильно-сортировочная установка, …»
_ITEM_CODE_FIRST = re.compile(
    r'^(?P<n>\d{1,2})\.\s*в\s+соответствии\s+с\s+'
    r'(?:Основн\w+\s+правил\w+\s+интерпретации|ОПИ)(?P<rules>.*?)\s*'
    r'в\s+(?P<level>товарной\s+позиции|подсубпозиции|субпозиции)\s+(?P<code>\d[\d\s]*\d)\b'
    r'.*?классифицир\w+\s+(?P<desc>.+?)\.?\s*$',
    re.IGNORECASE | re.DOTALL,
)


def _decode(chunk: bytes) -> str:
    try:
        return chunk.decode('utf-8')
    except UnicodeDecodeError:
        return chunk.decode('cp1251', errors='replace')


def _text(fragment: str) -> str:
    text = html.unescape(re.sub(r'<[^>]+>', ' ', fragment)).replace('​', '')
    return re.sub(r'\s+', ' ', text).strip()


def parse_list(page: bytes) -> list[dict]:
    """Rows of the decisions table: codes, number, date, link, title, status."""
    rows = []
    for chunk in page.split(b'<tr>')[2:]:  # [0] before the table, [1] the header row
        row = _decode(chunk.split(b'</tr>')[0])
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, flags=re.S)
        if len(cells) < 4:
            continue
        number = re.search(r'№\s*(\d+)', _text(cells[2]))
        date = re.search(r'(\d{2})\.(\d{2})\.(\d{4})', _text(cells[2]))
        link = re.search(r'href="([^"]+)"', cells[2])
        status = 'in_force'
        if re.search(r'отмен', row, re.I):
            status = 'cancelled'
        elif re.search(r'внесены\s+изменения', row, re.I):
            status = 'amended'
        rows.append({
            'n': _text(cells[0]).rstrip('.'),
            'codes': [re.sub(r'\D', '', c) for c in
                      re.findall(r'\d{4}(?:\s?\d{2})?(?:\s?\d{3})?(?:\s?\d)?', _text(cells[1]))],
            'number': number.group(1) if number else None,
            'date': f'{date.group(3)}-{date.group(2)}-{date.group(1)}' if date else None,
            'url': html.unescape(link.group(1)) if link else None,
            'title': _text(cells[3]),
            'status': status,
        })
    return rows


def docx_paragraphs(data: bytes) -> list[str]:
    import io

    xml = zipfile.ZipFile(io.BytesIO(data)).read('word/document.xml').decode('utf-8')
    paragraphs = re.findall(r'<w:p[ >].*?</w:p>', xml, flags=re.S)
    out = [html.unescape(''.join(re.findall(r'<w:t[^>]*>([^<]*)</w:t>', p))) for p in paragraphs]
    return [re.sub(r'\s+', ' ', p).strip() for p in out if p.strip()]


def pdf_paragraphs(data: bytes) -> list[str]:
    """PDF text joined into numbered paragraphs (lines starting with «N. »)."""
    import io

    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        text = '\n'.join(page.extract_text() or '' for page in pdf.pages)
    paragraphs, current = [], []
    for line in text.splitlines():
        if re.match(r'^\d{1,2}\.\s', line) and current:
            paragraphs.append(' '.join(current))
            current = []
        current.append(line.strip())
    if current:
        paragraphs.append(' '.join(current))
    return [re.sub(r'\s+', ' ', p).strip() for p in paragraphs]


def item_blocks(paragraphs: list[str]) -> list[str]:
    """Paragraphs regrouped into the numbered items of the operative part.

    Older texts run the preamble and every item into one paragraph, some move the
    product's characteristics into paragraphs of their own, and a one-item
    decision may have no number at all.
    """
    parts = [s.strip() for p in paragraphs
             for s in re.split(r'(?<=[:.;])\s*(?=\d{1,2}\.\s+\S)', p) if s.strip()]
    blocks: list[str] = []
    current: str | None = None
    operative = False
    for part in parts:
        if re.match(r'^\d{1,2}\.\s', part):
            if current:
                blocks.append(current)
            current, operative = part, True
        elif current is not None:
            current = f'{current} {part}'
        elif re.search(r'решил\s*а\s*:\s*$', part, re.IGNORECASE):
            operative = True
        elif operative:
            current = f'1. {part}'
    if current:
        blocks.append(current)
    return blocks


def parse_items(paragraphs: list[str]) -> list[dict]:
    """«N. <product> … классифицируется в <level> <code>» items of a decision."""
    items = []
    for block in item_blocks(paragraphs):
        match = _ITEM.search(block) or _ITEM_CODE_FIRST.search(block)
        if not match:
            continue
        items.append({
            'item': int(match['n']),
            'description': re.sub(r'\s+', ' ', match['desc']).strip(),
            # «Товарной номенклатуры … 1, 3(б) и 6» → «1, 3(б) и 6»
            'rules': re.sub(r'^\D+', '', re.sub(r'\s+', ' ', match['rules'])).strip(),
            'level': re.sub(r'\s+', ' ', match['level']).lower(),
            'code': re.sub(r'\D', '', match['code']),
        })
    return items


def russian_file(landing: str) -> tuple[str, str] | None:
    """(href, 'docx'|'pdf') of the Russian text on a decision page.

    The page links the text in every official language; the anchor reads
    «Рус», «Հայ», «Бел», «Қаз». DOCX is preferred: its paragraphs keep the items.
    """
    links = [
        (href, kind.lower(), _text(label))
        for href, kind, label in re.findall(
            r'<a[^>]+href="([^"]+\.(docx|pdf))"[^>]*>(.*?)</a>', landing, flags=re.I | re.S)
    ]
    russian = [(href, kind) for href, kind, label in links if label.startswith('Рус')]
    if not russian:
        return None
    return sorted(russian, key=lambda link: link[1] != 'docx')[0]


async def _get(client: httpx.AsyncClient, url: str, delay_s: float,
               retries: int = 3) -> httpx.Response:
    for attempt in range(retries + 1):
        await asyncio.sleep(delay_s if attempt == 0 else 10 * attempt)
        try:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()
            return response
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError):
            if attempt == retries:
                raise
    raise AssertionError('unreachable')


def uchet_url(date: str, number: str) -> str:
    """zakon.uchet.kz keeps EEC decisions as H<yy>EK<number, 6 digits>."""
    return f'{UCHET}/rus/docs/H{date[2:4]}EK{int(number):06d}'


def uchet_paragraphs(page: str) -> list[str]:
    body = page.split('itemprop="articleBody"', 1)[-1]
    return [p for p in (_text(x) for x in re.findall(r'<p[^>]*>(.*?)</p>', body, flags=re.S)) if p]


async def _fetch(client: httpx.AsyncClient, row: dict, out_dir: Path, delay_s: float) -> dict:
    """Text of one decision (cached by date and number) and its items; errors recorded.

    The EEC publishes the Russian text as DOCX or PDF; older PDFs are scans without
    a text layer. Then the text comes from zakon.uchet.kz, which republishes the
    decisions; the source of the text is recorded.
    """
    record = {**row, 'file': None, 'sha256': None, 'items': [], 'error': None,
              'text_source': None, 'text_url': None, 'text_sha256': None}
    stem = f"{row['date']}_{row['number']}"
    cached = sorted(p for p in out_dir.glob(f'{stem}.*') if p.suffix in ('.docx', '.pdf'))
    if cached:
        path = cached[0]
    else:
        landing = await _get(client, row['url'], delay_s)
        found = russian_file(landing.text)
        if found is None:
            record['error'] = 'no Russian DOCX or PDF on the decision page'
            return record
        href, kind = found
        file = await _get(client, str(httpx.URL(str(landing.url)).join(href)), delay_s)
        path = out_dir / f'{stem}.{kind}'
        path.write_bytes(file.content)
    data = path.read_bytes()
    record.update(file=path.name, sha256=hashlib.sha256(data).hexdigest(),
                  text_source=f'eec_{path.suffix[1:]}', text_url=row['url'])
    paragraphs = docx_paragraphs(data) if path.suffix == '.docx' else pdf_paragraphs(data)
    record['items'] = parse_items(paragraphs)
    if not record['items']:
        mirror = out_dir / f'{stem}.uchet.html'
        url = uchet_url(row['date'], row['number'])
        if not mirror.exists():
            mirror.write_bytes((await _get(client, url, delay_s)).content)
        page = mirror.read_bytes()
        record['items'] = parse_items(uchet_paragraphs(page.decode('utf-8', errors='replace')))
        if record['items']:
            record.update(text_source='zakon.uchet.kz', text_url=url,
                          text_sha256=hashlib.sha256(page).hexdigest())
    if not record['items']:
        record['error'] = 'no classification item recognised'
    return record


async def collect(out_dir: Path, *, since: str = '2017-01-01', delay_s: float = 1.0) -> list[dict]:
    """Download the list and the texts decided since `since` (cached); parse them."""
    out_dir.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(headers=HEADERS, timeout=60) as client:
        page = (await _get(client, LIST_URL, 0)).content
        (out_dir / 'list.html').write_bytes(page)
        decisions = []
        for row in parse_list(page):
            if not row['url'] or not row['number'] or not row['date']:
                record = {**row, 'items': [], 'error': 'no link, number or date in the list'}
            elif row['date'] < since:
                record = {**row, 'items': [], 'error': f'decided before {since}: not downloaded'}
            else:
                try:
                    record = await _fetch(client, row, out_dir, delay_s)
                except (httpx.HTTPError, zipfile.BadZipFile, KeyError, ValueError) as exc:
                    record = {**row, 'items': [], 'error': f'{type(exc).__name__}: {exc}'}
            decisions.append(record)
            logger.info('№ %s %s: %d items %s', row['number'], row['date'],
                        len(record['items']), record['error'] or '')
    (out_dir / 'decisions.json').write_text(
        json.dumps(decisions, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    return decisions
