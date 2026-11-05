"""Holdout: закрытые тестовые наборы и сборка пулов без утечек.

На holdout решение принимается один раз, после заморозки вопросов, порогов и
провайдера (docs/decision_engine_plan.md, §7, шаг 0.2):

- ``eaeu_pd_test400`` и его короткая версия ``eaeu_pd_test400_short``;
- ``eec_decisions_test``.

Манифест ``holdout.json`` закрепляет хеши этих файлов, исходные записи и
десятизначные коды. Если набор пересобран, ``Holdout.load`` падает.

Всё, на чём что-то подбирается, собирается через ``Holdout.pool``:

- ``calibration`` — пороги, политика сигналов, формулировки вопросов. Без записей
  holdout, без записей с теми же десятизначными кодами и без почти-дублей текста;
- ``replay`` — проверка спецификаций семейства. Без записей holdout и почти-дублей,
  но записи с тем же кодом остаются: иначе листья test нечем проверить.

Почти-дубль — коэффициент Жаккара по словесным триграммам не ниже 0,3. На снимке
предрешений 2026-10-18 выше порога — тот же товар с правками (иногда под другим
кодом: ступица 8708 50 910 9 против 8708 50 990 9). Ниже порога — соседние
записи того же заявителя: другой товар по тому же шаблону текста (рис круглозёрный
против басмати).

Запуск:
    python -m benchmark.holdout              # проверить манифест и dev-наборы
    python -m benchmark.holdout --write      # заморозить holdout (только при новом test)
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from benchmark.schema.case_v2 import CaseV2, load_cases

CASES_DIR = Path('benchmark/data/cases/v2')
MANIFEST = CASES_DIR / 'holdout.json'

HOLDOUT_SETS = ('eaeu_pd_test400', 'eaeu_pd_test400_short', 'eec_decisions_test')
#: Короткая версия test400 — те же записи с урезанным текстом: её тексты не
#: сравниваются, записи и коды совпадают с полной.
TEXT_SETS = ('eaeu_pd_test400', 'eec_decisions_test')
#: Наборы для подбора и анализа; проверяются ``python -m benchmark.holdout``.
DEV_SETS = ('eaeu_pd_dev150', 'eec_decisions_dev', 'passport_regression100')

SHINGLE_WORDS = 3
NEAR_DUPLICATE = 0.3

Purpose = Literal['calibration', 'replay']
#: Что исключается из пула каждого назначения.
RULES: dict[str, tuple[str, ...]] = {
    'calibration': ('record', 'code', 'text'),
    'replay': ('record', 'text'),
}

_RECORD = re.compile(r'запись ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})')


class HoldoutLeak(ValueError):
    """Пул пересекается с holdout."""


def source_of(case: CaseV2) -> str:
    """Исходная запись кейса: у полной и короткой версии она одна."""
    if case.provenance.kind == 'preliminary_decision':
        m = _RECORD.search(case.provenance.reference)
        if not m:
            raise ValueError(f'{case.id}: в ссылке нет id записи сборника')
        return f'pd:{m.group(1)}'
    if case.provenance.kind == 'eec_decision':
        return f"eec:{case.id.removeprefix('EEC-')}"
    return f'case:{case.id}'


def code_of(case: CaseV2) -> str | None:
    gold = case.gold.classification
    return gold.code if gold else None


def shingles(text: str) -> frozenset[str]:
    words = re.findall(r'[0-9a-zа-я]+', text.lower().replace('ё', 'е'))
    k = SHINGLE_WORDS
    return frozenset(' '.join(words[i:i + k]) for i in range(max(1, len(words) - k + 1)))


@dataclass(frozen=True)
class Leak:
    """Кейс пула, который нельзя использовать: чем он связан с holdout."""

    case_id: str
    kind: Literal['record', 'code', 'text']
    holdout: str  # кейс holdout (для кода — сам код)
    score: float | None = None

    def __str__(self) -> str:
        score = f' ({self.score:.2f})' if self.score is not None else ''
        return f'{self.case_id}: {self.kind} → {self.holdout}{score}'


@dataclass
class Pool:
    cases: list[CaseV2]
    excluded: list[Leak]

    def summary(self) -> dict[str, int]:
        return dict(Counter(leak.kind for leak in self.excluded))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Holdout:
    def __init__(self, sets: dict[str, list[CaseV2]]):
        self.sets = sets
        cases = [c for name in HOLDOUT_SETS for c in sets.get(name, [])]
        self.sources: dict[str, str] = {}
        for case in cases:
            self.sources.setdefault(source_of(case), case.id)
        self.codes10 = {code for c in cases if (code := code_of(c)) and len(code) == 10}
        self._texts = [(c.id, shingles(c.input.description))
                       for name in TEXT_SETS for c in sets.get(name, [])]
        self._index: dict[str, list[int]] = defaultdict(list)
        for i, (_, grams) in enumerate(self._texts):
            for gram in grams:
                self._index[gram].append(i)

    @classmethod
    def load(cls, manifest: Path = MANIFEST, cases_dir: Path = CASES_DIR) -> Holdout:
        """Holdout по манифесту; падает, если набор изменился после заморозки."""
        frozen = json.loads(manifest.read_text(encoding='utf-8'))
        sets = {}
        for name in HOLDOUT_SETS:
            path = cases_dir / f'{name}.jsonl'
            if _sha256(path) != frozen['sets'][name]['sha256']:
                raise HoldoutLeak(f'{path} изменён после заморозки holdout ({manifest})')
            sets[name] = load_cases(path)
        holdout = cls(sets)
        if sorted(holdout.sources) != frozen['sources'] or sorted(holdout.codes10) != frozen['codes10']:
            raise HoldoutLeak(f'{manifest} не совпадает с наборами: пересоздайте его с --write')
        return holdout

    def manifest(self) -> dict:
        return {
            'purpose': 'Закрытые наборы: прогон один раз после заморозки. Пулы подбора собираются '
                       'через benchmark.holdout.Holdout.pool и не пересекаются с ними.',
            'created_at': dt.datetime.now(dt.UTC).isoformat(timespec='seconds'),
            'rules': {
                'calibration': 'без записей holdout, их десятизначных кодов и почти-дублей текста',
                'replay': 'без записей holdout и почти-дублей текста',
                'near_duplicate': {'shingle_words': SHINGLE_WORDS, 'jaccard_at_least': NEAR_DUPLICATE,
                                   'texts_of': list(TEXT_SETS)},
            },
            'sets': {name: {'n': len(cases), 'sha256': _sha256(CASES_DIR / f'{name}.jsonl'),
                            'case_ids': sorted(c.id for c in cases)}
                     for name, cases in self.sets.items()},
            'sources': sorted(self.sources),
            'codes10': sorted(self.codes10),
        }

    def near_duplicate(self, text: str) -> tuple[str, float] | None:
        """Ближайший текст holdout, если сходство не ниже порога."""
        grams = shingles(text)
        common: Counter[int] = Counter()
        for gram in grams:
            common.update(self._index.get(gram, ()))
        best = None
        for i, n in common.items():
            score = n / len(grams | self._texts[i][1])
            if score >= NEAR_DUPLICATE and (best is None or score > best[1]):
                best = (self._texts[i][0], score)
        return best

    def leaks(self, case: CaseV2, purpose: Purpose) -> list[Leak]:
        rules = RULES[purpose]
        found = []
        if 'record' in rules and (other := self.sources.get(source_of(case))):
            found.append(Leak(case.id, 'record', other))
        if 'code' in rules and (code := code_of(case)) in self.codes10:
            found.append(Leak(case.id, 'code', code))
        if 'text' in rules and (dup := self.near_duplicate(case.input.description)):
            found.append(Leak(case.id, 'text', dup[0], round(dup[1], 3)))
        return found

    def pool(self, cases: Iterable[CaseV2], purpose: Purpose) -> Pool:
        """Кейсы, пригодные для ``purpose``, и причины исключения остальных."""
        kept, excluded = [], []
        for case in cases:
            found = self.leaks(case, purpose)
            if found:
                excluded.append(found[0])
            else:
                kept.append(case)
        return Pool(kept, excluded)

    def check(self, cases: Iterable[CaseV2], purpose: Purpose) -> list[CaseV2]:
        """Кейсы без изменений, если ни один не пересекается с holdout; иначе — ошибка."""
        cases = list(cases)
        leaks = [leak for case in cases for leak in self.leaks(case, purpose)]
        if leaks:
            shown = '; '.join(map(str, leaks[:10]))
            raise HoldoutLeak(f'{len(leaks)} пересечений с holdout ({purpose}): {shown}')
        return cases


def load_pool(path: Path, purpose: Purpose, holdout: Holdout | None = None) -> list[CaseV2]:
    """Кейсы пула из файла; падает, если пул пересекается с holdout."""
    return (holdout or Holdout.load()).check(load_cases(path), purpose)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--write', action='store_true', help='Заморозить holdout по текущим наборам')
    args = p.parse_args()
    if args.write:
        holdout = Holdout({name: load_cases(CASES_DIR / f'{name}.jsonl') for name in HOLDOUT_SETS})
        MANIFEST.write_text(json.dumps(holdout.manifest(), ensure_ascii=False, indent=2) + '\n',
                            encoding='utf-8')
        print(f'{MANIFEST}: {len(holdout.sources)} записей, {len(holdout.codes10)} десятизначных кодов')
    holdout = Holdout.load()
    print(f'holdout: {", ".join(f"{n} {len(c)}" for n, c in holdout.sets.items())}')
    for name in DEV_SETS:
        cases = load_cases(CASES_DIR / f'{name}.jsonl')
        replay, calibration = holdout.pool(cases, 'replay'), holdout.pool(cases, 'calibration')
        print(f'{name}: {len(cases)}; replay {len(replay.cases)}, calibration {len(calibration.cases)}'
              + ''.join(f'\n  − {leak}' for leak in calibration.excluded))
        if replay.excluded:
            raise HoldoutLeak(f'{name} пересекается с holdout по записям или текстам')


if __name__ == '__main__':
    main()
