"""Precedents for experiments: the product's BM25 index over a tuning pool, without leaks.

The search itself is ``feap.precedents`` (one implementation for the product and the
benchmark). This wrapper builds the index from benchmark cases instead of the
``classification_precedents`` table and removes what must not be found:

- the index is built from a pool (``Holdout.pool(..., 'calibration' | 'replay')``), so it
  never contains holdout records or their near-duplicates;
- ``exclude`` drops the records of the evaluated cases and every pool record that is
  a near-duplicate of one of them (word-trigram Jaccard ≥ 0.3).

``exclusion_ids`` gives the product the same rule for an end-to-end run: the ids of the
``classification_precedents`` rows to leave out (``PRECEDENTS_EXCLUDE_PATH``).
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from benchmark.holdout import NEAR_DUPLICATE, shingles, source_of
from benchmark.schema.case_v2 import CaseV2
from feap.precedents import Precedent, terms  # noqa: F401 — re-exported for scripts
from feap.precedents import PrecedentIndex as _Index


def _near(grams: list[frozenset[str]], text: str) -> bool:
    g = shingles(text)
    return any(g & other and len(g & other) / len(g | other) >= NEAR_DUPLICATE for other in grams)


class PrecedentIndex(_Index):
    """``feap.precedents.PrecedentIndex`` over pool cases; a precedent's ``id`` is the case id."""

    def __init__(self, pool: Iterable[CaseV2], *, exclude: Iterable[CaseV2] = (), k1: float = 1.2, b: float = 0.75):
        excluded = list(exclude)
        sources = {source_of(c) for c in excluded}
        grams = [shingles(c.input.description) for c in excluded]
        self.cases: list[CaseV2] = []
        self.dropped = Counter()
        for case in pool:
            if source_of(case) in sources:
                self.dropped['record'] += 1
            elif _near(grams, case.input.description):
                self.dropped['near_duplicate'] += 1
            else:
                self.cases.append(case)
        super().__init__(((c.id, c.gold.classification.code, c.input.description, None) for c in self.cases),
                         k1=k1, b=b)


def registry_id(case: CaseV2) -> str | None:
    """Id of the registry record behind a case (``classification_precedents.id``)."""
    source = source_of(case)
    return source.removeprefix('pd:') if source.startswith('pd:') else None


def exclusion_ids(all_ids: Iterable[str], allowed_pool: Iterable[CaseV2], evaluated: Iterable[CaseV2]) -> list[str]:
    """Rows of the product table to hide during an end-to-end run.

    Everything outside ``allowed_pool`` (holdout and its near-duplicates), the evaluated
    records and the pool records that are near-duplicates of them.
    """
    evaluated = list(evaluated)
    grams = [shingles(c.input.description) for c in evaluated]
    hidden = {registry_id(c) for c in evaluated}
    allowed = set()
    for case in allowed_pool:
        rid = registry_id(case)
        if rid and rid not in hidden and not _near(grams, case.input.description):
            allowed.add(rid)
    return sorted(set(all_ids) - allowed)

