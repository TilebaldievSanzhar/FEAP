"""Проверка одного кейса: условия → вопросы → ответы → статусы → сигналы (план, §5.2).

``verify`` никогда не меняет код каскада: он возвращает сигналы и флаги политик.
Числовые значения извлекаются один раз на ключ величины и могут прийти из кеша —
тогда все плечи сравнивают одинаковые числа, а различаются только ответами на
семантические вопросы (предрегистрация, §4).
"""
from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Iterable

from feap.cascade.evidence import source_spans
from feap.decision.backend import (
    ASSORTMENT_ID,
    ASSORTMENT_QUESTION,
    SEMANTIC_OPTIONS,
    ExtractionRequest,
    NumericExtractor,
    SemanticBackend,
    answer_status,
)
from feap.decision.conditions import CaseConditions, build_conditions
from feap.decision.contracts import (
    CallRecord,
    ConditionKind,
    DecisionAnswer,
    DecisionQuestion,
    EvidenceFact,
    Method,
    VerifyTrace,
)
from feap.decision.logic import compare
from feap.decision.rows import PositionRows
from feap.decision.signals import OwnStatus, compute_signals, evaluate, policy_flags

#: Версия вопросов, вариантов и типизации строк. Меняется при любой их правке:
#: прогоны с разными версиями не сравниваются (предрегистрация, §8).
QUESTIONS_VERSION = 'e2lite-1'

_LEADING = re.compile(r'^(?:с|со|на|при|в)\s+', re.IGNORECASE)


def questions_for(cc: CaseConditions) -> tuple[list[DecisionQuestion], dict[str, list[str]]]:
    """Семантические вопросы кейса; одинаковый вопрос в одинаковой ветке задаётся один раз."""
    seen: dict[tuple, str] = {}
    members: dict[str, list[str]] = {}
    questions = []
    for cid, c in cc.conditions.items():
        if c.kind is not ConditionKind.SEMANTIC or not c.question:
            continue
        key = (tuple(c.context), c.question)
        if key in seen:
            members[seen[key]].append(cid)
            continue
        seen[key] = cid
        members[cid] = [cid]
        questions.append(DecisionQuestion(id=cid, text=c.question, context=c.context, options=SEMANTIC_OPTIONS))
    return questions, members


def _quantity(text: str | None) -> str:
    return _LEADING.sub('', re.sub(r'\s+', ' ', (text or '').lower().replace('ё', 'е')).strip())


def _parent(cid: str) -> str:
    position, _, path = cid.partition('/')
    return f'{position}/{path.rsplit(".", 1)[0]}' if '.' in path else position


def _ancestors(cid: str) -> list[str]:
    chain = []
    while '/' in cid:
        cid = _parent(cid)
        chain.append(cid)
    return chain


def extraction_requests(cc: CaseConditions) -> tuple[list[ExtractionRequest], dict[str, str]]:
    """Ключи величин для числовых условий.

    Условие без названия величины («не более 3 мас.%») берёт его у ближайшего
    числового предка той же размерности («с содержанием жира …»), затем у соседей.
    Так одна величина извлекается один раз и сравнивается со всеми интервалами.
    """
    numeric = [c for c in cc.conditions.values() if c.kind is ConditionKind.NUMERIC and c.interval]
    requests: dict[str, ExtractionRequest] = {}
    key_of: dict[str, str] = {}
    # «двигатели номинальной выходной мощностью» — та же величина, что «номинальной выходной мощностью»
    names = {(_quantity(c.quantity), c.interval.dimension) for c in numeric if _quantity(c.quantity)}

    def shortest(name: str, dimension: str) -> str:
        tails = [n for n, d in names if d == dimension and (name == n or name.endswith(' ' + n))]
        return min(tails, key=len, default=name)

    for c in numeric:
        dimension = c.interval.dimension
        named = c.quantity or ''
        if not _quantity(named):
            related = [cc.conditions.get(a) for a in _ancestors(c.id)]
            related += [o for o in numeric if _parent(o.id) == _parent(c.id)]
            named = next((r.quantity for r in related
                          if r is not None and r.kind is ConditionKind.NUMERIC and r.interval
                          and r.interval.dimension == dimension and _quantity(r.quantity)), '')
        key = f'{shortest(_quantity(named), dimension) or "fork:" + _parent(c.id)}|{dimension}'
        key_of[c.id] = key
        name = key.rsplit('|', 1)[0]
        request = requests.setdefault(key, ExtractionRequest(
            key=key, quantity='' if name.startswith('fork:') else name, dimension=dimension,
            unit=c.interval.unit, context=c.context))
        request.conditions.append(c.text)
    return list(requests.values()), key_of


async def verify_case(
    *,
    case_id: str,
    description: str,
    chosen_code: str,
    alternatives: Iterable[str],
    trees: dict[str, PositionRows],
    backend: SemanticBackend,
    extractor: NumericExtractor | None = None,
    facts: dict[str, EvidenceFact | None] | None = None,
    cap: int = 15,
    headings: dict[str, list[tuple[str, str]]] | None = None,
) -> VerifyTrace:
    started = time.perf_counter()
    alternatives = list(alternatives)
    spans = source_spans(description)
    trace = VerifyTrace(case_id=case_id, arm=backend.name, questions_version=QUESTIONS_VERSION,
                        chosen_code=chosen_code, alternatives=alternatives, spans=spans)
    try:
        cc = build_conditions(trees, chosen_code, alternatives, cap=cap, headings=headings)
    except ValueError as exc:
        trace.error = str(exc)
        return trace
    questions, members = questions_for(cc)
    questions.append(ASSORTMENT_QUESTION)
    requests, key_of = extraction_requests(cc)
    known = dict(facts or {})
    missing = [r for r in requests if r.key not in known]

    async def no_facts() -> tuple[dict[str, EvidenceFact | None], list[CallRecord]]:
        return {}, []

    extract = extractor.extract(missing, spans) if missing and extractor else no_facts()
    (answers, calls), (new_facts, extract_calls) = await asyncio.gather(backend.answer(questions, spans), extract)
    known.update(new_facts)
    if missing and extractor is None:
        trace.notes.append(f'нет извлечения для {len(missing)} величин: числовые условия = U')

    by_question: dict[str, DecisionAnswer] = {a.question_id: a for a in answers}
    own: dict[str, OwnStatus] = {}
    for qid, cids in members.items():
        status, method, detail = answer_status(by_question.get(qid), n_spans=len(spans))
        for cid in cids:
            own[cid] = (status, method, detail, [])
    for cid, key in key_of.items():
        status, detail, presumed = compare(cc.conditions[cid].interval, known.get(key))
        own[cid] = (status, Method.DETERMINISTIC, detail, presumed)
    statuses = evaluate(cc, own)
    assortment = answer_status(by_question.get(ASSORTMENT_ID))[0]
    signals = compute_signals(cc, statuses, assortment)

    all_calls = [*calls, *extract_calls]
    trace.conditions = list(cc.conditions.values())
    trace.chosen_path = cc.chosen
    trace.alternative_paths = cc.alternatives
    trace.questions = questions
    trace.answers = answers
    trace.facts = {r.key: known.get(r.key) for r in requests}
    trace.statuses = list(statuses.values())
    trace.signals = signals
    trace.flags = policy_flags(signals)
    trace.calls = all_calls
    trace.cost_usd = round(sum(c.cost_usd or 0 for c in all_calls), 6)
    trace.latency_ms = round((time.perf_counter() - started) * 1000, 1)
    trace.notes += cc.notes
    return trace
