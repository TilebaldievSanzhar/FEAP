"""Precedents: similar decisions of the customs authorities, retrieved by BM25.

A decision of the EAEU collection (registry ЕС 999) is a real product with the
code an authority gave it. For a product description the index returns the
most similar decisions; their codes are candidates for navigation, their texts
are evidence for the selector.

No leakage by construction:

- the index is built from a tuning pool (``Holdout.pool(..., 'calibration')``),
  so it never contains holdout records, their codes or near-duplicates;
- ``exclude`` drops the records of the evaluated cases and every pool record
  that is a near-duplicate of one of them (word-trigram Jaccard ≥ 0.3).

The tokenizer is deliberately simple: lower case, ё → е, words of three or more
letters cut to six characters (a crude stem), numbers kept whole.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from benchmark.holdout import NEAR_DUPLICATE, shingles, source_of
from benchmark.schema.case_v2 import CaseV2

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
    case_id: str
    code: str
    description: str
    score: float


class PrecedentIndex:
    def __init__(self, pool: Iterable[CaseV2], *, exclude: Iterable[CaseV2] = (), k1: float = 1.2, b: float = 0.75):
        excluded = list(exclude)
        sources = {source_of(c) for c in excluded}
        grams = [shingles(c.input.description) for c in excluded]
        self.cases: list[CaseV2] = []
        self.dropped = Counter()
        for case in pool:
            if source_of(case) in sources:
                self.dropped['record'] += 1
                continue
            g = shingles(case.input.description)
            if any(g & other and len(g & other) / len(g | other) >= NEAR_DUPLICATE for other in grams):
                self.dropped['near_duplicate'] += 1
                continue
            self.cases.append(case)
        self.k1, self.b = k1, b
        self.tf = [Counter(terms(c.input.description)) for c in self.cases]
        self.lengths = [sum(tf.values()) for tf in self.tf]
        self.avg = sum(self.lengths) / max(len(self.lengths), 1)
        df = Counter(t for tf in self.tf for t in tf)
        n = len(self.cases)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.postings: dict[str, list[int]] = defaultdict(list)
        for i, tf in enumerate(self.tf):
            for t in tf:
                self.postings[t].append(i)

    def search(self, description: str, k: int = 10) -> list[Precedent]:
        scores: Counter[int] = Counter()
        for t in set(terms(description)):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i in self.postings[t]:
                f = self.tf[i][t]
                norm = 1 - self.b + self.b * self.lengths[i] / self.avg
                scores[i] += idf * f * (self.k1 + 1) / (f + self.k1 * norm)
        return [Precedent(self.cases[i].id, self.cases[i].gold.classification.code,
                          self.cases[i].input.description, round(s, 3)) for i, s in scores.most_common(k)]
