"""Движок фактов: банк вопросов о товаре, ответы decision-моделей, маршрутизация к группам.

docs/decision_facts_engine.md. Decision-модель отвечает на вопросы о товаре выбором из закрытых
вариантов и отдаёт распределение по ним. Тариф в этих вопросах не толкуется: связь фактов с
группами задают профили (составлены один раз) и маршрутизатор, обученный на реестре предрешений
(``feap.decision.facts.router``).

Факты товара — ``Facts``: id вопроса → распределение по его вариантам. Сбой ответа — это
``unknown``, а не догадка.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from feap.cascade.evidence import source_spans
from feap.decision.contracts import CallRecord, DecisionAnswer, DecisionQuestion

HERE = Path(__file__).parent
BANK_V1 = HERE / 'bank_v1.yaml'
UNKNOWN = 'unknown'

Facts = dict[str, dict[str, float]]


@dataclass(frozen=True)
class FactQuestion:
    id: str
    text: str
    options: dict[str, str]
    absent: str | None = None  # условный вопрос: вариант «не относится»


@dataclass(frozen=True)
class FactBank:
    version: str
    instructions: str
    questions: tuple[FactQuestion, ...]
    sha256: str

    def __getitem__(self, question_id: str) -> FactQuestion:
        for q in self.questions:
            if q.id == question_id:
                return q
        raise KeyError(question_id)

    @property
    def ids(self) -> list[str]:
        return [q.id for q in self.questions]

    def decision_questions(self) -> list[DecisionQuestion]:
        return [DecisionQuestion(id=q.id, text=q.text, options=dict(q.options)) for q in self.questions]

    def instructions_for(self, question: DecisionQuestion) -> str:
        return f'{question.text}\n{self.instructions}'


def file_sha256(path: Path) -> str:
    """sha256 с LF-концами строк: одинаковый в рабочей копии Windows и в git."""
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def load_bank(path: Path = BANK_V1) -> FactBank:
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    questions = []
    for raw in data['questions']:
        options = {str(k): ' '.join(str(v).split()) for k, v in raw['options'].items()}
        q = FactQuestion(id=raw['id'], text=raw['text'], options=options, absent=raw.get('absent'))
        if UNKNOWN not in q.options:
            raise ValueError(f'{q.id}: нет варианта {UNKNOWN}')
        if q.absent is not None and q.absent not in q.options:
            raise ValueError(f'{q.id}: absent={q.absent!r} не среди вариантов')
        questions.append(q)
    ids = [q.id for q in questions]
    if len(set(ids)) != len(ids):
        raise ValueError('повторяются id вопросов')
    return FactBank(version=data['version'], instructions=' '.join(data['instructions'].split()),
                    questions=tuple(questions), sha256=file_sha256(path))


def facts_from_answers(bank: FactBank, answers: Sequence[DecisionAnswer]) -> Facts:
    """Ответы исполнителя → распределения. Без распределения — весь вес на выбранный вариант."""
    by_id = {a.question_id: a for a in answers}
    facts: Facts = {}
    for q in bank.questions:
        a = by_id.get(q.id)
        if a is None or a.status != 'ok':
            facts[q.id] = {UNKNOWN: 1.0}
        elif a.distribution:
            kept = {k: max(float(v), 0.0) for k, v in a.distribution.items() if k in q.options}
            total = sum(kept.values())
            facts[q.id] = {k: v / total for k, v in kept.items() if v > 0} if total > 0 else {UNKNOWN: 1.0}
        elif a.label in q.options:
            facts[q.id] = {a.label: 1.0}
        else:
            facts[q.id] = {UNKNOWN: 1.0}
    return facts


def top_label(distribution: dict[str, float]) -> str:
    """Наиболее вероятный вариант; при равенстве — первый по алфавиту (детерминированно)."""
    return min(distribution, key=lambda k: (-distribution[k], k)) if distribution else UNKNOWN


async def ask_facts(executor, bank: FactBank,
                    description: str) -> tuple[Facts, list[DecisionAnswer], list[CallRecord]]:
    """Все вопросы банка одним пакетом; исполнитель — ``OpenRouterDecisions`` с инструкцией банка."""
    answers, calls = await executor.answer(bank.decision_questions(), source_spans(description))
    return facts_from_answers(bank, answers), answers, calls
