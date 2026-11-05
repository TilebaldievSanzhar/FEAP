"""Прецеденты: решения таможенных органов по похожим товарам.

Источник — «Сборник принятых предварительных решений таможенных органов
государств — членов ЕАЭС по классификации товаров» (ЕС 999, справочник 1999 НСИ
ЕАЭС), таблица ``classification_precedents`` (scripts/seed/precedents.py).
Решение — реальный товар и код, который ему присвоил таможенный орган.

Поиск — BM25 по описаниям. Токенизация намеренно простая: нижний регистр, ё → е,
слова от трёх букв, обрезанные до шести символов (грубая основа), числа целиком.
Индекс строится один раз на процесс из таблицы.

Для оценки на наборах из того же реестра оцениваемые записи нельзя находить
самих себя: ``PRECEDENTS_EXCLUDE_PATH`` — файл с id записей, которые в индекс не
попадают (по одному в строке; его готовит бенчмарк).

Прецедент — подсказка, а не правило: похожий по словам товар может отличаться
признаком, решающим по примечаниям (смесь против отдельного соединения). Поэтому
каскад добавляет позиции прецедентов к кандидатам L3, а выбирает сам — с текстами
позиций, примечаниями и пояснениями.
"""
from __future__ import annotations

import asyncio
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

_STOP = set('и в во на с со по из для от до не или а но что как это его ее их при под над к ко о об же'.split())


def terms(text: str) -> list[str]:
    out = []
    for word in re.findall(r'[0-9a-zа-я]+', text.lower().replace('ё', 'е')):
        if word.isdigit():
            out.append(word)
        elif len(word) >= 3 and word not in _STOP:
            out.append(word[:6])
    return out


@dataclass(frozen=True)
class Precedent:
    id: str
    code: str
    description: str
    country: str | None
    score: float


class PrecedentIndex:
    """BM25 по описаниям решений (k1 = 1,2, b = 0,75)."""

    def __init__(self, rows: Iterable[tuple[str, str, str, str | None]], *, k1: float = 1.2, b: float = 0.75):
        self.rows = list(rows)  # (id, code, description, country)
        self.k1, self.b = k1, b
        self.tf = [Counter(terms(description)) for _, _, description, _ in self.rows]
        self.lengths = [sum(tf.values()) for tf in self.tf]
        self.avg = sum(self.lengths) / max(len(self.lengths), 1)
        df = Counter(t for tf in self.tf for t in tf)
        n = len(self.rows)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.postings: dict[str, list[int]] = defaultdict(list)
        for i, tf in enumerate(self.tf):
            for t in tf:
                self.postings[t].append(i)

    def __len__(self) -> int:
        return len(self.rows)

    def search(self, text: str, k: int = 10) -> list[Precedent]:
        scores: Counter[int] = Counter()
        for t in set(terms(text)):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i in self.postings[t]:
                f = self.tf[i][t]
                norm = 1 - self.b + self.b * self.lengths[i] / self.avg
                scores[i] += idf * f * (self.k1 + 1) / (f + self.k1 * norm)
        out = []
        for i, score in scores.most_common(k):
            rid, code, description, country = self.rows[i]
            out.append(Precedent(rid, code, description, country, round(score, 3)))
        return out


def excluded_ids(path: str | Path | None) -> set[str]:
    if not path:
        return set()
    return {line.strip() for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()}


_index: PrecedentIndex | None = None
_lock = asyncio.Lock()


async def get_index(db) -> PrecedentIndex:
    """Индекс процесса из ``classification_precedents`` без исключённых записей."""
    global _index
    if _index is None:
        async with _lock:
            if _index is None:
                from sqlalchemy import select

                from feap.config import settings
                from feap.db.models import ClassificationPrecedent as P

                skip = excluded_ids(settings.PRECEDENTS_EXCLUDE_PATH)
                result = await db.execute(select(P.id, P.code, P.description, P.country).order_by(P.id))
                _index = PrecedentIndex(row for row in result.all() if row[0] not in skip)
    return _index


async def search(db, text: str, k: int = 10) -> list[Precedent]:
    return (await get_index(db)).search(text, k)


def reset() -> None:
    """Сбросить индекс (после загрузки реестра или смены исключений)."""
    global _index
    _index = None
