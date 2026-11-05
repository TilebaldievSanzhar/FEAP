"""Плечо V3: decision-модели через OpenRouter Decisions API.

``POST https://openrouter.ai/api/alpha/decisions`` — state и именованные вопросы;
ответ на вопрос типа ``choice`` — выбранный ключ и распределение по вариантам,
отказ — ``{"type": "refusal"}`` внутри ответа 200. Модели: ``typesafe/jev-1.14``,
``liquid/d1.1``, ``microsoft/microsoft-decision-1`` и др. (модальность
``text->decisions``; в каталоге чат-моделей ``/api/v1/models`` их нет).

Все вопросы кейса уходят одним запросом: ответы на них независимы по устройству
модели, state (фрагменты описания) передаётся один раз. Если вопросов больше
``questions_per_request`` или вход больше контекста модели, запрос делится, а не
усекается; вопрос, который не помещается даже один, получает ``overflow``.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable, Sequence

import httpx

from feap.decision.backend import (
    READING_RULES,
    ContextOverflow,
    check_answers,
    estimate_tokens,
    question_text,
)
from feap.decision.contracts import CallRecord, DecisionAnswer, DecisionQuestion
from feap.decision.providers.openrouter import BudgetExhausted

DECISIONS_URL = 'https://openrouter.ai/api/alpha/decisions'

#: Контекст моделей по каталогу OpenRouter (5–6 ноября 2026), с запасом на служебные токены.
CONTEXT = {
    'typesafe/jev-1.14': 262_144,
    'liquid/d1.1': 65_536,
    'microsoft/microsoft-decision-1': 32_768,
    'cloudflare/clef': 65_537,
    'cloudflare/clef-flash': 16_384,
    'upstage/solar-decide': 524_288,
    'inception/mercury-decide': 65_536,
    'openai/gpt-6-luna-decisions': 1_050_000,
}
_RULES = ' '.join(READING_RULES)


def _instructions(question: DecisionQuestion) -> str:
    return f'{question_text(question)}\nОтвечай только по описанию товара в state. {_RULES}'


def _state(spans: Sequence[str]) -> dict:
    return {'description': [{'source_id': i, 'text': text} for i, text in enumerate(spans, 1)]}


def _parse(question: DecisionQuestion, answer: dict | None, call_id: str) -> DecisionAnswer:
    if not isinstance(answer, dict):
        return DecisionAnswer(question_id=question.id, status='invalid', error='нет ответа', call_id=call_id)
    if answer.get('type') == 'refusal':
        return DecisionAnswer(question_id=question.id, status='refused', error='refusal', call_id=call_id)
    if answer.get('type') != 'choice':
        return DecisionAnswer(question_id=question.id, status='invalid', error=f'тип ответа {answer.get("type")!r}',
                              call_id=call_id)
    probabilities = answer.get('probabilities')
    distribution = {str(k): float(v) for k, v in probabilities.items()} if isinstance(probabilities, dict) else None
    return DecisionAnswer(question_id=question.id, label=answer.get('choice'), distribution=distribution,
                          call_id=call_id)


class OpenRouterDecisions:
    """V3: семантические вопросы верификатора как ``choice`` с тремя статусами.

    ``instructions`` задаёт текст инструкции вопроса; по умолчанию — условие ЕТТ с правилами
    чтения верификатора. Движок фактов передаёт свой текст (``FactBank.instructions_for``).
    """

    def __init__(self, model: str, *, api_key: str | None = None, concurrency: int = 4, timeout: float = 120.0,
                 questions_per_request: int = 40, transport: httpx.AsyncBaseTransport | None = None,
                 instructions: Callable[[DecisionQuestion], str] | None = None):
        self.model = model
        self.name = f'V3:{model}'
        self._instructions = instructions or _instructions
        self.api_key = api_key
        self.timeout = timeout
        self.questions_per_request = questions_per_request
        self.max_input_tokens = int(CONTEXT.get(model, 32_768) * 0.8)
        self._semaphore = asyncio.Semaphore(concurrency)
        self._transport = transport

    def _key(self) -> str:
        if self.api_key is None:
            from feap.config import settings

            self.api_key = settings.OPENROUTER_API_KEY
        return self.api_key

    def _chunks(self, questions: list[DecisionQuestion], spans: list[str]) -> list[list[DecisionQuestion]]:
        """Группы вопросов, каждая со state помещается в контекст; одиночный великан — отдельно."""
        base = estimate_tokens(str(_state(spans)))
        chunks: list[list[DecisionQuestion]] = []
        current: list[DecisionQuestion] = []
        size = base
        for q in questions:
            cost = estimate_tokens(self._instructions(q) + str(q.options)) + 10
            if current and (len(current) >= self.questions_per_request or size + cost > self.max_input_tokens):
                chunks.append(current)
                current, size = [], base
            current.append(q)
            size += cost
        if current:
            chunks.append(current)
        return chunks

    async def answer(self, questions: list[DecisionQuestion],
                     spans: list[str]) -> tuple[list[DecisionAnswer], list[CallRecord]]:
        if not questions:
            return [], []
        results = await asyncio.gather(*(self._request(chunk, spans) for chunk in self._chunks(questions, spans)))
        answers = [a for chunk_answers, _ in results for a in chunk_answers]
        calls = [call for _, call in results if call]
        return check_answers(questions, answers), calls

    async def _request(self, chunk: list[DecisionQuestion],
                       spans: list[str]) -> tuple[list[DecisionAnswer], CallRecord | None]:
        keys = {f'q{i}': q for i, q in enumerate(chunk, 1)}
        body = {
            'model': self.model,
            'state': _state(spans),
            'questions': {key: {'type': 'choice', 'instructions': self._instructions(q), 'criteria': dict(q.options)}
                          for key, q in keys.items()},
        }
        if estimate_tokens(str(body)) > self.max_input_tokens:
            error = str(ContextOverflow(f'~{estimate_tokens(str(body))} токенов > {self.max_input_tokens}'))
            return [DecisionAnswer(question_id=q.id, status='overflow', error=error) for q in chunk], None
        record = CallRecord(id=uuid.uuid4().hex[:12], model=self.model, questions=[q.id for q in chunk])
        data = None
        async with self._semaphore, httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
            for attempt in (1, 2, 3):
                started = time.perf_counter()
                try:
                    response = await client.post(DECISIONS_URL, json=body,
                                                 headers={'Authorization': f'Bearer {self._key()}'})
                except httpx.HTTPError as exc:
                    record.error = f'{type(exc).__name__}: {exc}'
                    record.latency_ms = (time.perf_counter() - started) * 1000
                    continue
                record.latency_ms = (time.perf_counter() - started) * 1000
                if response.status_code == 402:
                    raise BudgetExhausted(f'Error code: 402 - {response.text[:200]}')
                if response.status_code in (429, 500, 502, 503, 524, 529) and attempt < 3:
                    record.error = f'HTTP {response.status_code}'
                    await asyncio.sleep(2 * attempt)
                    continue
                if response.status_code != 200:
                    record.error = f'HTTP {response.status_code}: {response.text[:300]}'
                    break
                data = response.json()
                record.error = None
                break
        if data is None:
            status = 'error'
            return [DecisionAnswer(question_id=q.id, status=status, error=record.error, call_id=record.id)
                    for q in chunk], record
        usage = data.get('usage') or {}
        record = record.model_copy(update={'revision': data.get('model'), 'usage': usage,
                                           'cost_usd': float(usage.get('cost') or 0)})
        answers = data.get('answers') or {}
        return [_parse(q, answers.get(key), record.id) for key, q in keys.items()], record
