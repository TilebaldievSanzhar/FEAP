"""Плечо V3 через OpenRouter Decisions API на поддельном транспорте: без сети и без денег."""
import json

import httpx
import pytest

from feap.decision.backend import SEMANTIC_OPTIONS, answer_status
from feap.decision.contracts import DecisionQuestion, Method, Status
from feap.decision.providers.openrouter import BudgetExhausted
from feap.decision.providers.openrouter_decisions import DECISIONS_URL, OpenRouterDecisions

QUESTIONS = [DecisionQuestion(id=f'0207/1.{i}', text=text, context=['Мясо птицы'], options=SEMANTIC_OPTIONS)
             for i, text in enumerate(['замороженные', 'необваленные', 'индеек'], 1)]
SPANS = ['Окорочка куриные замороженные.', 'Необваленные.']


def transport(handler):
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return handler(calls[-1], len(calls), request)
    return httpx.MockTransport(respond), calls


def ok(answers, cost=0.00002):
    return httpx.Response(200, json={'answers': answers, 'model': 'typesafe/jev-1.14-20261014', 'provider': 'TypeSafe',
                                     'usage': {'input_tokens': 900, 'output_tokens': 30, 'cost': cost}})


def choice(key, p):
    return {'type': 'choice', 'choice': key, 'confidence': 0.5, 'probabilities': p}


async def test_one_request_per_case_with_choice_questions_and_distributions():
    def handler(body, n, request):
        assert request.url == DECISIONS_URL and request.headers['Authorization'] == 'Bearer k'
        return ok({'q1': choice('supported', {'supported': 0.9, 'contradicted': 0.05, 'not_stated': 0.05}),
                   'q2': choice('contradicted', {'supported': 0.1, 'contradicted': 0.7, 'not_stated': 0.2}),
                   'q3': {'type': 'refusal'}})
    mock, calls = transport(handler)
    answers, records = await OpenRouterDecisions('typesafe/jev-1.14', api_key='k', transport=mock).answer(
        QUESTIONS, SPANS)
    body = calls[0]
    assert len(calls) == 1 and body['model'] == 'typesafe/jev-1.14'
    assert body['state']['description'][0] == {'source_id': 1, 'text': 'Окорочка куриные замороженные.'}
    assert body['questions']['q1']['type'] == 'choice' and set(body['questions']['q1']['criteria']) == set(
        SEMANTIC_OPTIONS)
    assert '«замороженные»' in body['questions']['q1']['instructions']
    assert [answer_status(a)[0] for a in answers] == [Status.T, Status.U, Status.U]  # F только при p ≥ 0,8
    assert answers[2].status == 'refused' and answer_status(answers[2])[1] is Method.FAILURE
    assert records[0].cost_usd == 0.00002 and records[0].revision.endswith('20261014')


async def test_questions_are_split_into_requests_not_truncated():
    def handler(body, n, request):
        return ok({k: choice('not_stated', {'supported': 0, 'contradicted': 0, 'not_stated': 1})
                   for k in body['questions']})
    mock, calls = transport(handler)
    answers, records = await OpenRouterDecisions('liquid/d1.1', api_key='k', transport=mock,
                                                 questions_per_request=2).answer(QUESTIONS, SPANS)
    assert [len(c['questions']) for c in calls] == [2, 1] and len(records) == 2
    assert all(a.status == 'ok' for a in answers)


async def test_out_of_credits_stops_and_server_errors_are_retried():
    mock, _ = transport(lambda body, n, request: httpx.Response(402, json={'error': {'code': 402}}))
    with pytest.raises(BudgetExhausted):
        await OpenRouterDecisions('liquid/d1.1', api_key='k', transport=mock).answer(QUESTIONS[:1], SPANS)

    def flaky(body, n, request):
        if n == 1:
            return httpx.Response(502, json={'error': {'code': 502}})
        return ok({'q1': choice('supported', {'supported': 1.0, 'contradicted': 0, 'not_stated': 0})})
    mock, calls = transport(flaky)
    answers, _ = await OpenRouterDecisions('liquid/d1.1', api_key='k', transport=mock).answer(QUESTIONS[:1], SPANS)
    assert len(calls) == 2 and answers[0].status == 'ok'


async def test_bad_request_and_invalid_distribution_are_failures():
    mock, _ = transport(lambda body, n, request: httpx.Response(400, json={'error': {'code': 400}}))
    answers, records = await OpenRouterDecisions('liquid/d1.1', api_key='k', transport=mock).answer(
        QUESTIONS[:1], SPANS)
    assert answers[0].status == 'error' and 'HTTP 400' in records[0].error

    mock, _ = transport(lambda body, n, request: ok({'q1': choice('supported', {'supported': 0.5, 'maybe': 0.5})}))
    answers, _ = await OpenRouterDecisions('liquid/d1.1', api_key='k', transport=mock).answer(QUESTIONS[:1], SPANS)
    assert answers[0].status == 'invalid'


async def test_a_question_too_large_for_the_model_overflows_without_a_call():
    mock, calls = transport(lambda body, n, request: ok({}))
    huge = ['слово ' * 40_000]
    answers, records = await OpenRouterDecisions('microsoft/microsoft-decision-1', api_key='k',
                                                 transport=mock).answer(QUESTIONS[:1], huge)
    assert answers[0].status == 'overflow' and calls == [] and records == []
