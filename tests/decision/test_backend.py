"""Проверка ответов исполнителя до их толкования."""
from feap.decision.backend import (
    SEMANTIC_OPTIONS,
    answer_status,
    check_answers,
    question_text,
    spans_block,
)
from feap.decision.contracts import DecisionAnswer, DecisionQuestion, Method, Status

T, F, U = Status.T, Status.F, Status.U
Q = [DecisionQuestion(id=f'q{i}', text=f'условие {i}', options=SEMANTIC_OPTIONS) for i in (1, 2, 3)]


def test_every_question_gets_exactly_one_checked_answer():
    answers = check_answers(Q, [
        DecisionAnswer(question_id='q2', label='supported'),
        DecisionAnswer(question_id='q2', label='contradicted'),  # повтор — берётся первый
        DecisionAnswer(question_id='q9', label='supported'),     # чужой id — отбрасывается
        DecisionAnswer(question_id='q3', label='maybe'),
    ])
    assert [(a.question_id, a.status, a.label) for a in answers] == [
        ('q1', 'invalid', None), ('q2', 'ok', 'supported'), ('q3', 'invalid', 'maybe')]
    assert answers[0].error == 'нет ответа'


def test_distribution_must_use_the_options_and_sum_to_one():
    def check(distribution):
        return check_answers(Q[:1], [DecisionAnswer(question_id='q1', distribution=distribution)])[0]
    assert check({'supported': 0.7, 'contradicted': 0.2, 'not_stated': 0.1}).status == 'ok'
    assert 'сумма' in check({'supported': 0.5, 'contradicted': 0.2}).error
    assert 'не из вопроса' in check({'supported': 0.9, 'maybe': 0.1}).error
    assert check({'supported': 1.1, 'contradicted': -0.1}).status == 'invalid'


def test_failures_and_refusals_are_unknown_not_refuted():
    for status in ('refused', 'error', 'overflow', 'invalid'):
        assert answer_status(DecisionAnswer(question_id='q', status=status))[:2] == (U, Method.FAILURE)
    assert answer_status(None)[0] is U


def test_distribution_thresholds_keep_weak_refutations_unknown():
    def status(**p):
        return answer_status(DecisionAnswer(question_id='q', distribution=p))[0]
    assert status(supported=0.55, contradicted=0.3, not_stated=0.15) is T
    assert status(supported=0.1, contradicted=0.7, not_stated=0.2) is U  # F только при p ≥ 0,8
    assert status(supported=0.05, contradicted=0.85, not_stated=0.1) is F


def test_llm_answer_needs_a_real_source():
    def status(label, ids):
        return answer_status(DecisionAnswer(question_id='q', label=label, source_ids=ids, cites=True), n_spans=3)
    assert status('supported', [1]) == (T, Method.MODEL, None)
    assert status('supported', [])[0] is U
    assert status('contradicted', [4])[0] is U
    assert status('not_stated', [])[0] is U
    # decision-модель не цитирует: её метка принимается как есть
    assert answer_status(DecisionAnswer(question_id='q', label='contradicted'))[0] is F


def test_question_and_source_texts():
    question = DecisionQuestion(id='q', text='необваленные', context=['Мясо птицы', 'части тушек'],
                                options=SEMANTIC_OPTIONS)
    text = question_text(question)
    assert '«необваленные»' in text and '«Мясо птицы» → «части тушек»' in text
    block = spans_block(['Окорочка куриные.', 'Замороженные.'])
    assert '{"source_id": 2, "text": "Замороженные."}' in block
