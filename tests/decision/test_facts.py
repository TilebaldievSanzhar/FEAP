"""Движок фактов: банк вопросов, ответы decision-моделей, маршрутизация к группам — без сети."""
import json
import math

import httpx
import pytest
import yaml

from feap.decision.contracts import DecisionAnswer
from feap.decision.facts import UNKNOWN, ask_facts, facts_from_answers, load_bank, top_label
from feap.decision.facts.router import NaiveBayes, combine, load_profiles, profile_scores, rank
from feap.decision.providers.openrouter_decisions import OpenRouterDecisions


@pytest.fixture(scope='module')
def bank():
    return load_bank()


@pytest.fixture(scope='module')
def profiles(bank):
    return load_profiles(bank)


def facts(**answers):
    """kind='machine_electrical' → {kind: {machine_electrical: 1}}; кортеж (вариант, p) — мягкий ответ."""
    out = {}
    for qid, value in answers.items():
        out[qid] = dict(value) if isinstance(value, (list, tuple)) else {value: 1.0}
    return out


# ── банк ──────────────────────────────────────────────────────────────────────

def test_bank_v1_is_closed_and_every_question_can_say_unknown(bank):
    assert bank.version == 'facts-1' and len(bank.questions) == 16
    assert all(UNKNOWN in q.options for q in bank.questions)
    conditional = {q.id: q.absent for q in bank.questions if q.absent}
    assert conditional == {'food_base': 'not_food', 'food_preparation': 'not_food', 'chemical_form': 'not_chemical',
                           'textile_form': 'not_textile', 'metal_form': 'not_metal', 'vehicle_kind': 'not_vehicle'}
    q = bank.decision_questions()[0]
    assert q.id == 'kind' and 'общеизвестные свойства' in bank.instructions_for(q)
    assert len(bank.sha256) == 64


def test_a_bank_without_unknown_is_rejected(tmp_path):
    path = tmp_path / 'bank.yaml'
    path.write_text(yaml.safe_dump({'version': 'x', 'instructions': 'и', 'questions': [
        {'id': 'kind', 'text': 'Что это?', 'options': {'a': 'А'}}]}, allow_unicode=True), encoding='utf-8')
    with pytest.raises(ValueError, match='unknown'):
        load_bank(path)


def test_answers_become_distributions_and_failures_become_unknown(bank):
    answers = [
        DecisionAnswer(question_id='kind', label='vehicle', distribution={'vehicle': 0.6, 'machine_electrical': 0.2}),
        DecisionAnswer(question_id='material', label='ferrous'),
        DecisionAnswer(question_id='state', status='refused'),
        DecisionAnswer(question_id='drive', label='чужой вариант'),
    ]
    f = facts_from_answers(bank, answers)
    assert f['kind'] == pytest.approx({'vehicle': 0.75, 'machine_electrical': 0.25})
    assert f['material'] == {'ferrous': 1.0}
    assert f['state'] == f['drive'] == f['user'] == {UNKNOWN: 1.0}
    assert top_label({'a': 0.4, 'b': 0.4, 'c': 0.2}) == 'a' and top_label({}) == UNKNOWN


async def test_facts_are_asked_in_one_request_with_the_bank_instructions(bank):
    calls = []

    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        keys = list(body['questions'])
        return httpx.Response(200, json={'model': 'liquid/d1.1-rev', 'usage': {'cost': 0.0002}, 'answers': {
            k: {'type': 'choice', 'choice': UNKNOWN, 'probabilities': {UNKNOWN: 1.0}} for k in keys}})
    executor = OpenRouterDecisions('liquid/d1.1', api_key='k', transport=httpx.MockTransport(respond),
                                   instructions=bank.instructions_for)
    f, answers, records = await ask_facts(executor, bank, 'Насос центробежный. Мощность 5 кВт.')
    body = calls[0]
    assert len(calls) == 1 and len(body['questions']) == 16
    assert body['state']['description'][1] == {'source_id': 2, 'text': 'Мощность 5 кВт.'}
    first = body['questions']['q1']
    assert first['instructions'].startswith('Что это за товар') and 'Условие ТН ВЭД' not in first['instructions']
    assert set(first['criteria']) == set(bank['kind'].options)
    assert all(v == {UNKNOWN: 1.0} for v in f.values()) and records[0].cost_usd == 0.0002


# ── профили ───────────────────────────────────────────────────────────────────

def test_profiles_cover_96_groups_and_default_conditional_questions_to_absent(profiles):
    assert len(profiles.groups) == 96 and '77' not in profiles.groups
    assert profiles.groups['84']['food_base'] == frozenset({'not_food'})   # не указан → «не относится»
    assert profiles.groups['84']['vehicle_kind'] is None                   # any
    assert profiles.groups['04']['food_base'] == frozenset({'dairy', 'eggs_honey', 'mixed'})
    assert 'user' not in profiles.groups['04']                            # не указан, не условный → без ограничения


def test_profiles_reject_options_that_are_not_in_the_bank(bank, tmp_path):
    path = tmp_path / 'p.yaml'
    path.write_text(yaml.safe_dump({'version': 'p', 'bank': bank.version, 'groups': {'84': {'kind': ['машина']}}},
                                   allow_unicode=True), encoding='utf-8')
    with pytest.raises(ValueError, match='нет таких вариантов'):
        load_profiles(bank, path)


def test_law_separates_an_instrument_from_a_machine(profiles):
    gauge = facts(kind=[('instrument', 0.6), ('machine_electrical', 0.4)], function='measurement',
                  principle='electronic', drive='mains', state='article')
    scores = profile_scores(gauge, profiles)
    assert rank(scores)[0] == '90' and scores['90'] > scores['84'] > scores['04']


def test_law_keeps_plain_dairy_in_04_and_unknown_is_compatible(profiles):
    cheese = facts(kind='animal_product', material='animal', food_base='dairy', food_preparation='natural',
                   function='food', drive='none', principle='passive')
    assert rank(profile_scores(cheese, profiles))[0] == '04'
    nothing = profile_scores({}, profiles)
    assert set(nothing.values()) == {0.0}


# ── маршрутизатор по данным ───────────────────────────────────────────────────

def test_naive_bayes_learns_groups_from_soft_answers(bank):
    pump = facts(kind='machine_electrical', function='fluids', principle='mechanical')
    lamp = facts(kind='machine_electrical', function='lighting', principle='electric')
    nb = NaiveBayes(bank, ['84', '85', '90'], alpha=1.0).fit([(pump, '84')] * 5 + [(lamp, '85')] * 5)
    assert rank(nb.scores(facts(kind='machine_electrical', function='fluids')))[0] == '84'
    assert rank(nb.scores(facts(function=[('lighting', 0.7), ('fluids', 0.3)])))[0] == '85'
    q = bank['function']
    assert math.isclose(math.exp(nb.log_likelihood('90', 'function', 'fluids')), 1 / len(q.options))


def test_combine_sums_scores_and_rank_breaks_ties_by_code():
    assert combine({'84': -1.0, '85': -2.0}, {'84': -3.0, '85': 0.0}) == {'84': -4.0, '85': -2.0}
    assert rank({'85': 0.0, '84': 0.0, '90': 1.0}) == ['90', '84', '85']
