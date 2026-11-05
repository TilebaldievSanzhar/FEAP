"""Replay и оценка верификатора: альтернативы из трассы, решение §8, метки эталона."""
import json

from benchmark.scripts.score_verify import bootstrap_diff, gold_labels, h1_decision
from benchmark.scripts.verify_replay import trace_alternatives
from benchmark.schema.case_v2 import CaseV2
from feap.decision.conditions import build_conditions
from feap.decision.contracts import Status, VerifyTrace
from tests.decision.test_rows import tree


def _record(case_id, name, data):
    return {'case_id': case_id, 'response_format': {'type': 'json_schema', 'json_schema': {'name': name}},
            'raw_content': json.dumps(data, ensure_ascii=False)}


def test_alternatives_come_from_every_level_of_the_trace(tmp_path):
    path = tmp_path / 'trace.jsonl'
    rows = [
        _record('M02', 'ShortlistResult', {'shortlisted': ['0207', '0210']}),
        _record('M02', 'PositionResult', {'candidates': [{'position': '0207'}]}),
        _record('M02', 'SubpositionResult', {'candidates': [{'subposition': '0207 14'}, {'subposition': '020713'}]}),
        _record('M02', 'SubsubpositionShortlistResult', {'candidates': [{'code_8': '0207 14 60'}]}),
        _record('M02', 'SubsubpositionResult', {'candidates': [{'code_10': '0207 14 600 9'}]}),
        _record('M02', 'GroupResult', {'candidates': [{'group': '02'}]}),  # группа — не альтернатива верификатора
        {**_record('M02', 'PositionResult', {}), 'raw_content': '{broken'},
        _record('X', 'ProductCard', {'candidates': []}),
    ]
    path.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows), encoding='utf-8')
    assert trace_alternatives(path) == {'M02': ['0207', '0210', '020714', '020713', '02071460', '0207146009']}
    assert trace_alternatives(None) == {}


def test_bootstrap_is_paired_and_reproducible():
    pairs = [(True, False)] * 6 + [(False, False)] * 3 + [(True, True)]
    first, second = bootstrap_diff(pairs), bootstrap_diff(pairs)
    assert first == second and first['diff'] == 0.6
    assert first['ci95'][0] > 0 and first['ci95'][1] <= 1
    assert bootstrap_diff([]) is None


def _row(recall, flags, low):
    return {'recall_wrong': {'share': recall}, 'flags_correct': {'share': flags},
            'minus_v0': {'diff': recall - 0.11, 'ci95': [low, 0.9]}}


def test_decision_rows_of_the_preregistration():
    assert h1_decision({'P1': _row(0.5, 0.15, 0.01), 'P2': _row(0.2, 0.1, -0.1),
                        'P3': _row(0.2, 0.1, -0.1)}) == 'H1 подтверждена'
    # recall ≥ 50%, но интервал разницы с V0 захватывает ноль — только «частично»
    assert h1_decision({'P1': _row(0.5, 0.15, -0.02), 'P2': _row(0.2, 0.1, -0.1),
                        'P3': _row(0.2, 0.1, -0.1)}) == 'H1 частично'
    assert h1_decision({'P1': _row(0.29, 0.1, 0.0), 'P2': _row(0.45, 0.3, 0.1),
                        'P3': _row(0.6, 0.4, 0.2)}) == 'H1 не подтверждена'


def _case(code, opi):
    return CaseV2.model_validate({
        'id': 'M02', 'track': 'passport', 'split': 'regression', 'tier': 'D',
        'provenance': {'kind': 'synthetic', 'reference': 'ручная'},
        'input': {'description': 'Окорочка куриные замороженные'},
        'gold': {'classification': {'code': code, 'verification': 'official', 'sufficiency': 'unknown',
                                    'opi_expected': opi}},
    })


def test_gold_labels_cover_the_gold_path_and_its_specific_siblings():
    t = tree('0207')
    cc = build_conditions({'0207': t}, '0207147009', ['02071460'])
    trace = VerifyTrace(case_id='M02', arm='V1', questions_version='x', chosen_code='0207147009',
                        conditions=list(cc.conditions.values()))
    labels = gold_labels(trace, _case('0207146009', ['ОПИ 1']), {'0207': t})
    expected = {(cid, kind): status for cid, kind, status in labels}
    legs = t.node('02071460').id
    other = t.node('02071470').id
    assert expected[(legs, 'semantic')] is Status.T             # путь эталона
    assert expected[(other, 'residual')] is Status.F            # «прочие» рядом с эталоном
    breasts = next(c for c in cc.conditions.values() if c.text == 'грудки и куски из них').id
    assert expected[(breasts, 'semantic')] is Status.F           # конкретный сосед эталона
    assert all(kind != 'regime' for _, kind, _ in labels)
    # без «простых» правил соседи не размечаются
    only_path = gold_labels(trace, _case('0207146009', ['ОПИ 1', 'ОПИ 3б']), {'0207': t})
    assert {s for _, _, s in only_path} == {Status.T}


def test_error_level_and_candidate_coverage_of_the_baseline():
    from benchmark.scripts.baseline_report import coverage, error_level
    assert error_level('0207146009', '0207147009') == 'eight_digit'
    assert error_level('9401490000', '9401710009') == 'subheading'
    assert error_level('7604210000', '7610909000') == 'heading'
    assert error_level('7604210000', '8544499509') == 'group'
    assert error_level('2204219800', '2204219600') == 'eight_digit'
    assert error_level('8708509109', '8708509909') == 'eight_digit'
    assert error_level('85015220', '8501522001') == 'beyond_gold'
    seen = {'L3': {'7604', '7610'}, 'L4': {'761010', '761090'}, 'L5': {'7610901000'}}
    assert coverage('7604210000', seen) == {'L2_group': False, 'L3_heading': True, 'L4_subheading': False,
                                            'L5_leaf': False}
    assert coverage('7604', seen)['L4_subheading'] is None
