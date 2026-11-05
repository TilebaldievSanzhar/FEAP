"""Контракты decision-движка (docs/decision_engine_plan.md, §5.3).

Исходные ответы исполнителя хранятся отдельно от их толкования: распределение
или метка — в ``DecisionAnswer``, статус условия и способ его получения — в
``ConditionStatus``. Сбой исполнителя — статус выполнения, а не свидетельство о
товаре: условие с таким ответом получает U.
"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Status(str, Enum):
    """Трёхзначный статус условия: подтверждено, опровергнуто, не установлено."""

    T = 'T'
    F = 'F'
    U = 'U'


class ConditionKind(str, Enum):
    SEMANTIC = 'semantic'  # смысл решает исполнитель
    NUMERIC = 'numeric'    # число и единицу извлекает модель, сравнивает код
    RESIDUAL = 'residual'  # чистое «прочие»: отрицание предшествующих соседей
    REGIME = 'regime'      # сноска 5), квоты: из описания не выводится


class Method(str, Enum):
    MODEL = 'model'
    DETERMINISTIC = 'deterministic'
    PRESUMPTION = 'presumption'
    LOGIC = 'logic'
    FAILURE = 'failure'


class Interval(BaseModel):
    """Числовой интервал условия в базовой единице своей величины."""

    dimension: str
    unit: str  # базовая единица: кг, л, м, Вт…
    low: Decimal | None = None
    low_inclusive: bool = False
    high: Decimal | None = None
    high_inclusive: bool = False
    text: str = ''  # границы как в ЕТТ: «более 750 Вт, но не более 7,5 кВт»

    def contains(self, value: Decimal) -> bool:
        if self.low is not None and (value < self.low or (value == self.low and not self.low_inclusive)):
            return False
        if self.high is not None and (value > self.high or (value == self.high and not self.high_inclusive)):
            return False
        return True


class Condition(BaseModel):
    """Условие строки ЕТТ или текста товарной позиции."""

    id: str                # '0207' — текст позиции, '0207/1.2.1' — строка под ней
    position: str
    text: str              # как в ЕТТ, со сноской
    context: list[str] = Field(default_factory=list)  # позиция и строки выше, сверху вниз
    kind: ConditionKind
    residual: bool = False  # «прочие»: чистое или с уточнением («креветки прочие»)
    question: str | None = None  # что спрашивается у исполнителя; None — решается без модели
    interval: Interval | None = None
    quantity: str | None = None  # величина числового условия: «с содержанием жира»
    footnote: str | None = None
    interpretive: bool = False  # ссылается на позиции, субпозиции или примечания
    has_number: bool = False    # число в семантическом условии
    source: str = 'raw_node_text'


class Presumption(BaseModel):
    """Читательская презумпция: значение по умолчанию, видимое в trace."""

    attribute: str
    value: str
    scope: str
    source: str
    status: Literal['draft', 'replayed', 'team_reviewed', 'expert_reviewed'] = 'draft'


class EvidenceFact(BaseModel):
    """Значение величины из описания: число, единица и фрагменты-источники."""

    attribute: str
    value: Decimal | None = None
    value_max: Decimal | None = None  # описание даёт диапазон «от … до …»
    unit: str | None = None
    source_ids: list[int] = Field(default_factory=list)
    origin: Literal['stated', 'presumed', 'user_answer', 'inferred'] = 'stated'
    note: str | None = None


class DecisionQuestion(BaseModel):
    id: str
    text: str
    context: list[str] = Field(default_factory=list)
    options: dict[str, str]  # ключ варианта → определение, видимое исполнителю


class CallRecord(BaseModel):
    """Один вызов исполнителя: модель, расход, время, исход."""

    id: str
    model: str
    revision: str | None = None
    questions: list[str] = Field(default_factory=list)
    usage: dict | None = None
    cost_usd: float | None = None
    latency_ms: float | None = None
    finish_reason: str | None = None
    error: str | None = None


class DecisionAnswer(BaseModel):
    question_id: str
    status: Literal['ok', 'refused', 'invalid', 'error', 'overflow'] = 'ok'
    label: str | None = None
    distribution: dict[str, float] | None = None
    source_ids: list[int] = Field(default_factory=list)
    cites: bool = False  # исполнитель обязан ссылаться на фрагменты (LLM)
    call_id: str | None = None
    error: str | None = None


class ConditionStatus(BaseModel):
    condition_id: str
    status: Status          # итог строки: собственное условие ∧ остаток
    own: Status             # собственное условие (для чистого «прочие» — T)
    residual: Status | None = None  # остаток для «прочие»
    method: Method
    detail: str | None = None
    presumed: list[str] = Field(default_factory=list)


SignalName = Literal['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7']


class Signal(BaseModel):
    name: SignalName
    strength: Literal['strong', 'medium', 'weak', 'question']
    conditions: list[str] = Field(default_factory=list)
    detail: str = ''


class VerifyTrace(BaseModel):
    """Проверка одного кейса одним плечом: всё, что нужно для отчёта и повтора."""

    case_id: str
    arm: str
    questions_version: str
    chosen_code: str
    alternatives: list[str] = Field(default_factory=list)
    spans: list[str] = Field(default_factory=list)
    conditions: list[Condition] = Field(default_factory=list)
    chosen_path: list[str] = Field(default_factory=list)
    alternative_paths: dict[str, list[str]] = Field(default_factory=dict)
    questions: list[DecisionQuestion] = Field(default_factory=list)
    answers: list[DecisionAnswer] = Field(default_factory=list)
    facts: dict[str, EvidenceFact | None] = Field(default_factory=dict)
    statuses: list[ConditionStatus] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)
    flags: dict[str, bool] = Field(default_factory=dict)
    calls: list[CallRecord] = Field(default_factory=list)
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    notes: list[str] = Field(default_factory=list)
    error: str | None = None
