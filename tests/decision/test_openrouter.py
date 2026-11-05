"""Адаптер V1/V2 и извлечение чисел на поддельном провайдере: без сети и без денег."""
import json
from types import SimpleNamespace

import pytest

import feap.llm.client as llm_client
from feap.decision.backend import SEMANTIC_OPTIONS, ExtractionRequest, answer_status
from feap.decision.contracts import DecisionQuestion, Method, Status
from feap.decision.providers.openrouter import BudgetExhausted, OpenRouterExtractor, OpenRouterLLM


def response(content, *, refusal=None, finish='stop', cost=0.0001):
    message = SimpleNamespace(content=content, refusal=refusal)
    usage = {'prompt_tokens': 100, 'completion_tokens': 10, 'cost': cost}
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)],
                           usage=SimpleNamespace(model_dump=lambda: usage), model='google/gemini-3.5-flash-lite-001')


class FakeProvider:
    def __init__(self, reply):
        self.reply = reply  # (kwargs, n-й вызов) → ответ или исключение
        self.calls: list[dict] = []

    def _build_extra_body(self, model):
        return {'provider': {'sort': 'price'}}

    def _get_client(self):
        return object()

    async def _call_api(self, client, **kwargs):
        self.calls.append(kwargs)
        result = self.reply(kwargs, len(self.calls))
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def provider(monkeypatch):
    def install(reply):
        fake = FakeProvider(reply)
        monkeypatch.setattr(llm_client, 'get_provider', lambda: fake)
        return fake
    return install


QUESTIONS = [DecisionQuestion(id=f'q{i}', text=text, options=SEMANTIC_OPTIONS)
             for i, text in enumerate(['замороженные', 'необваленные'], 1)]
SPANS = ['Окорочка куриные замороженные.', 'Необваленные.']


def _user(kwargs):
    return kwargs['messages'][1]['content']


async def test_single_question_calls_cite_and_record_cost(provider):
    fake = provider(lambda kw, n: response(json.dumps(
        {'source_ids': [1], 'status': 'supported'} if '«замороженные»' in _user(kw)
        else {'source_ids': [], 'status': 'not_stated'})))
    answers, calls = await OpenRouterLLM().answer(QUESTIONS, SPANS)
    assert [(a.question_id, a.label, a.source_ids, a.cites) for a in answers] == [
        ('q1', 'supported', [1], True), ('q2', 'not_stated', [], True)]
    assert len(calls) == 2 and all(c.cost_usd == 0.0001 and c.revision.endswith('-001') for c in calls)
    sent = fake.calls[0]
    assert sent['temperature'] == 0.0 and sent['response_format']['json_schema']['strict'] is True
    assert _user(sent).startswith('SOURCE') and '{"source_id": 1' in _user(sent)


async def test_refusal_is_a_failure_not_a_refutation(provider):
    provider(lambda kw, n: response(None, refusal='не могу ответить'))
    answers, _ = await OpenRouterLLM().answer(QUESTIONS[:1], SPANS)
    assert answers[0].status == 'refused'
    assert answer_status(answers[0])[:2] == (Status.U, Method.FAILURE)


async def test_broken_json_is_retried_once_and_both_calls_are_paid(provider):
    provider(lambda kw, n: response('{"status": "supp' if n == 1 else '{"source_ids": [2], "status": "contradicted"}'))
    answers, calls = await OpenRouterLLM().answer(QUESTIONS[:1], SPANS)
    assert answers[0].label == 'contradicted' and calls[0].cost_usd == pytest.approx(0.0002)


async def test_transport_errors_leave_an_error_answer(provider):
    provider(lambda kw, n: TimeoutError('slow'))
    answers, calls = await OpenRouterLLM().answer(QUESTIONS[:1], SPANS)
    assert answers[0].status == 'error' and 'TimeoutError' in calls[0].error


async def test_out_of_credits_stops_the_run(provider):
    provider(lambda kw, n: RuntimeError('Error code: 402 - insufficient credits'))
    with pytest.raises(BudgetExhausted):
        await OpenRouterLLM().answer(QUESTIONS[:1], SPANS)


async def test_overflow_is_reported_before_any_call(provider):
    fake = provider(lambda kw, n: response('{}'))
    answers, calls = await OpenRouterLLM(max_input_tokens=10).answer(QUESTIONS, SPANS)
    assert {a.status for a in answers} == {'overflow'} and calls == [] and fake.calls == []


async def test_batch_asks_again_only_for_missing_answers(provider):
    def reply(kwargs, n):
        ids = kwargs['response_format']['json_schema']['schema']['properties']['answers']['items'][
            'properties']['id']['enum']
        answered = ids[:1] if n == 1 else ids
        return response(json.dumps({'answers': [{'id': i, 'source_ids': [1], 'status': 'supported'}
                                                for i in answered]}))
    fake = provider(reply)
    answers, calls = await OpenRouterLLM(batch=True).answer(QUESTIONS, SPANS)
    assert [a.status for a in answers] == ['ok', 'ok'] and len(calls) == 2
    second = fake.calls[1]['response_format']['json_schema']['schema']['properties']['answers']['items']
    assert second['properties']['id']['enum'] == ['q2']


async def test_batch_without_answers_after_two_calls_marks_them_invalid(provider):
    provider(lambda kw, n: response(json.dumps({'answers': []})))
    answers, calls = await OpenRouterLLM(batch=True).answer(QUESTIONS, SPANS)
    assert [a.status for a in answers] == ['invalid', 'invalid'] and len(calls) == 2


REQUEST = ExtractionRequest(key='мощностью|power', quantity='мощностью', dimension='power', unit='Вт',
                            conditions=['мощностью не более 37 кВт'])


@pytest.mark.parametrize('payload, value, note', [
    ({'status': 'stated', 'value': '3.5', 'value_max': None, 'unit': 'кВт', 'source_ids': [1]}, '3.5', None),
    ({'status': 'not_stated', 'value': None, 'value_max': None, 'unit': None, 'source_ids': []}, None, 'не указано'),
    ({'status': 'stated', 'value': '3.5', 'value_max': None, 'unit': 'кВт', 'source_ids': []}, None,
     'значение без ссылки на фрагмент'),
    ({'status': 'stated', 'value': 'около трёх', 'value_max': None, 'unit': 'кВт', 'source_ids': [1]}, None,
     "не число: 'около трёх'"),
])
async def test_extraction_needs_a_number_and_a_source(provider, payload, value, note):
    provider(lambda kw, n: response(json.dumps(payload)))
    facts, calls = await OpenRouterExtractor().extract([REQUEST], SPANS)
    fact = facts[REQUEST.key]
    assert (str(fact.value) if fact.value is not None else None) == value and fact.note == note
    assert len(calls) == 1
