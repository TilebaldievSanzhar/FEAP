"""Маршрутизация от фактов товара к группам ТН ВЭД.

Три оценки, все — логарифмические, сравнимы внутри одной оценки:

- ``profile_scores`` — скомпилированный тариф: насколько распределения ответов совместимы с
  профилем группы (``profiles_v1.yaml``); несовместимость стоит ``log(eps)``, unknown совместим
  со всем;
- ``NaiveBayes`` — обученная на реестре модель «факты → группа»: мягкие счётчики по
  распределениям ответов, сглаживание Лапласа, равномерный prior по группам;
- ``combine`` — сумма оценок (закон + данные).

Используется распределение целиком, а не только наиболее вероятный ответ: это и есть выбор по
распределению вероятностей.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from feap.decision.facts import HERE, UNKNOWN, FactBank, Facts, file_sha256

PROFILES_V1 = HERE / 'profiles_v1.yaml'
ANY = 'any'


@dataclass(frozen=True)
class Profiles:
    version: str
    sha256: str
    #: группа → вопрос → допустимые варианты (None — без ограничения)
    groups: dict[str, dict[str, frozenset[str] | None]]


def load_profiles(bank: FactBank, path: Path = PROFILES_V1) -> Profiles:
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    if data.get('bank') != bank.version:
        raise ValueError(f'профили для банка {data.get("bank")!r}, а банк {bank.version!r}')
    groups: dict[str, dict[str, frozenset[str] | None]] = {}
    for group, spec in data['groups'].items():
        group = str(group).zfill(2)
        spec = spec or {}
        unknown_questions = set(spec) - set(bank.ids)
        if unknown_questions:
            raise ValueError(f'{group}: нет таких вопросов в банке: {sorted(unknown_questions)}')
        constraints: dict[str, frozenset[str] | None] = {}
        for q in bank.questions:
            value = spec.get(q.id)
            if value is None:
                if q.absent is not None:
                    constraints[q.id] = frozenset({q.absent})
                continue
            if value == ANY:
                constraints[q.id] = None
                continue
            allowed = frozenset(str(v) for v in value)
            bad = allowed - set(q.options)
            if bad:
                raise ValueError(f'{group}.{q.id}: нет таких вариантов: {sorted(bad)}')
            constraints[q.id] = allowed
        groups[group] = constraints
    return Profiles(version=data['version'], sha256=file_sha256(path), groups=groups)


def _dist(facts: Facts, question_id: str) -> Mapping[str, float]:
    return facts.get(question_id) or {UNKNOWN: 1.0}


def profile_scores(facts: Facts, profiles: Profiles, *, eps: float = 0.05) -> dict[str, float]:
    """log-совместимость фактов с профилем каждой группы; полная совместимость — 0."""
    full = math.log(1 + eps)
    out = {}
    for group, constraints in profiles.groups.items():
        score = 0.0
        for qid, allowed in constraints.items():
            if allowed is None:
                continue
            d = _dist(facts, qid)
            p = d.get(UNKNOWN, 0.0) + sum(d.get(o, 0.0) for o in allowed)
            score += math.log(eps + min(p, 1.0)) - full
        out[group] = score
    return out


class NaiveBayes:
    """«Факты → группа» по реестру: P(вариант | группа, вопрос) с мягкими счётчиками."""

    def __init__(self, bank: FactBank, groups: Iterable[str], *, alpha: float = 1.0):
        self.bank = bank
        self.groups = sorted(groups)
        self.alpha = alpha
        self.n = {g: 0.0 for g in self.groups}
        self.counts = {g: {q.id: {o: 0.0 for o in q.options} for q in bank.questions} for g in self.groups}

    def fit(self, rows: Iterable[tuple[Facts, str]]) -> NaiveBayes:
        for facts, group in rows:
            if group not in self.n:
                continue
            self.n[group] += 1
            for q in self.bank.questions:
                for option, p in _dist(facts, q.id).items():
                    if option in self.counts[group][q.id]:
                        self.counts[group][q.id][option] += p
        return self

    def log_likelihood(self, group: str, question_id: str, option: str) -> float:
        q = self.bank[question_id]
        return math.log((self.counts[group][question_id].get(option, 0.0) + self.alpha)
                        / (self.n[group] + self.alpha * len(q.options)))

    def scores(self, facts: Facts) -> dict[str, float]:
        out = {}
        for group in self.groups:
            score = 0.0
            for q in self.bank.questions:
                for option, p in _dist(facts, q.id).items():
                    if p > 0 and option in q.options:
                        score += p * self.log_likelihood(group, q.id, option)
            out[group] = score
        return out


def combine(*scores: Mapping[str, float], weights: Iterable[float] | None = None) -> dict[str, float]:
    weights = list(weights) if weights is not None else [1.0] * len(scores)
    groups = set.intersection(*(set(s) for s in scores))
    return {g: sum(w * s[g] for w, s in zip(weights, scores)) for g in groups}


def rank(scores: Mapping[str, float]) -> list[str]:
    """Группы по убыванию оценки; при равенстве — по коду."""
    return sorted(scores, key=lambda g: (-scores[g], g))
