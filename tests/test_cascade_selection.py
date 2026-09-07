"""Regression checks for model output silently changed by the cascade."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import importlib

import pytest
from pydantic import TypeAdapter, ValidationError

from feap.cascade.levels.base import CascadeState
from feap.cascade.levels.level4 import Level4Subposition
from feap.cascade.levels.level5 import Level5Subsubposition
from feap.cascade.levels.level5 import _get_top_branches
from feap.cascade.selection import choice_model, classify_choice, selected_candidate
from feap.schemas.codes import Code4, Code6, Code8, Code10
from feap.schemas.group import GroupResult
from feap.schemas.subposition import SubpositionResult
from feap.schemas.subsubposition import SubsubpositionResult
from feap.schemas.subsubposition import SubsubpositionShortlistResult


@pytest.mark.parametrize('annotation,raw,expected', [
    (Code4, '85.16', '8516'),
    (Code6, '8516 40', '851640'),
    (Code6, '8516\u00a040', '851640'),
    (Code8, '8516.40.00', '85164000'),
    (Code10, '8516 40 000 0', '8516400000'),
    (Code10, '0402\u202f10\u202f190\u202f0', '0402101900'),
])
def test_display_codes_normalized_before_membership(annotation, raw, expected):
    assert TypeAdapter(annotation).validate_python(raw) == expected


@pytest.mark.parametrize('raw', ['8516 — выбран утюг', '8516, 8509', '851', '851640', 8516])
def test_code_is_not_extracted_from_prose_or_truncated(raw):
    with pytest.raises(ValidationError):
        TypeAdapter(Code4).validate_python(raw)


def l4_result(code='8516 40'):
    return SubpositionResult(
        candidates=[
            {'subposition': '851690', 'subposition_text': 'части',
             'reasoning': 'Не подходит: товар целый', 'confidence': 0},
            {'subposition': '8516 40', 'subposition_text': 'электроутюги',
             'reasoning': 'Целый электрический утюг', 'confidence': 1},
        ],
        selected_subposition=code, confidence=1,
    )


def test_candidate_order_does_not_determine_confidence():
    result = l4_result()
    chosen = selected_candidate(result.candidates, 'subposition', result.selected_subposition)
    assert chosen.confidence == 1
    assert chosen.reasoning == 'Целый электрический утюг'


def test_request_schema_limits_choice_without_reordering_sgr():
    bound = choice_model(SubpositionResult, {'selected_subposition': {'851640', '851690'}})
    schema = bound.model_json_schema()
    assert schema['properties']['selected_subposition']['enum'] == ['', '851640', '851690']
    assert list(schema['properties']) == list(SubpositionResult.model_fields)
    assert 'enum' not in SubpositionResult.model_json_schema()['properties']['selected_subposition']
    assert bound.model_validate(l4_result().model_dump()).selected_subposition == '851640'
    with pytest.raises(ValidationError):
        bound.model_validate(l4_result('851699').model_dump())
    candidate_schema = schema['$defs']['SubpositionCandidate']['properties']['subposition']
    assert candidate_schema['enum'] == ['851640', '851690']


def test_l5_prefix_derived_before_schema_validation():
    result = SubsubpositionResult(selected_code_10='0402101900', selected_code_8='040210')
    assert result.selected_code_8 == '04021019'


async def test_invalid_choice_repaired_once_without_picking_last_child():
    call = AsyncMock(side_effect=[l4_result('851699'), l4_result()])
    result = await classify_choice(
        classify_fn=call, choices={'selected_subposition': {'851640', '851690'}},
        level=4, system_prompt='sys', user_message='original', output_model=SubpositionResult,
    )
    assert result.selected_subposition == '851640'
    assert call.await_count == 2
    assert call.call_args.kwargs['user_message'].startswith('original')


async def test_repeated_invalid_choice_fails_instead_of_guessing():
    call = AsyncMock(return_value=l4_result('851699'))
    with pytest.raises(ValueError, match='not an available code'):
        await classify_choice(
            classify_fn=call, choices={'selected_subposition': {'851640', '851690'}},
            level=4, user_message='product',
        )
    assert call.await_count == 2


async def test_clarification_can_leave_code_blank():
    result = l4_result('')
    result.missing_parameters = ['мощность']
    assert await classify_choice(
        classify_fn=AsyncMock(return_value=result),
        choices={'selected_subposition': {'851640'}}, level=4, user_message='product',
    ) is result


async def test_group_backtrack_can_leave_decision_blank():
    bound = choice_model(GroupResult, {'selected_group': {'90'}})
    result = bound.model_validate({
        'selected_group': '', 'candidates': [{
            'group': '90', 'group_name': 'Инструменты', 'exclusion_check': 'Исключён',
            'reasoning': 'Нужен другой раздел', 'confidence': 0,
        }],
        'backtrack_trigger': {'target_level': 1, 'excluded_code': 'XVIII',
                              'reason': 'Мебель относится к другому разделу'},
    })
    assert await classify_choice(
        classify_fn=AsyncMock(return_value=result), choices={'selected_group': {'90'}},
        level=2, user_message='Кресло',
    ) is result


async def test_blank_decision_without_control_signal_is_invalid():
    call = AsyncMock(return_value=l4_result(''))
    with pytest.raises(ValueError):
        await classify_choice(
            classify_fn=call, choices={'selected_subposition': {'851640'}},
            level=4, user_message='product',
        )
    assert call.await_count == 2


async def test_transport_failure_is_not_retried_by_choice_validator():
    call = AsyncMock(side_effect=RuntimeError('network unavailable'))
    with pytest.raises(RuntimeError):
        await classify_choice(
            classify_fn=call, choices={'selected_subposition': {'851640'}},
            level=4, user_message='product',
        )
    assert call.await_count == 1


async def test_l4_formatted_iron_never_becomes_parts(monkeypatch):
    import feap.cascade.levels.level4 as module
    nodes = [SimpleNamespace(code='851640', name='электроутюги'),
             SimpleNamespace(code='851690', name='части')]
    monkeypatch.setattr(module, 'get_node_by_code', AsyncMock(
        return_value=SimpleNamespace(id=1, section_roman=None)))
    monkeypatch.setattr(module, 'get_children', AsyncMock(return_value=nodes))
    for name in ['get_subposition_notes', 'get_cross_group_definitions',
                 'get_explanations_for_position', 'get_opi_rules']:
        monkeypatch.setattr(module, name, AsyncMock(return_value=[]))
    monkeypatch.setattr(module, 'classify', AsyncMock(return_value=l4_result()))
    state = CascadeState('Утюг')
    state.position, state.group = '8516', '85'
    state.product_card = {'original_description': 'Утюг', 'normalized_description': 'Утюг'}
    result = await Level4Subposition().run(state, None)
    assert result.selected_subposition == state.subposition == '851640'
    assert state.chain[-1].reasoning == 'Целый электрический утюг'
    assert state.chain[-1].confidence == 1


def test_l5_derived_codes_follow_validated_leaf():
    result = SubsubpositionResult(
        selected_code_10='0402 10 190 0', selected_code_8='04021099',
        final_code='неверное форматирование', confidence=0.9,
    )
    state = CascadeState('Молоко')
    Level5Subsubposition()._finalize(
        state, result, [SimpleNamespace(code='0402101900', name='молоко')], '040210',
    )
    assert result.selected_code_8 == '04021019'
    assert result.final_code == '0402 10 190 0'
    assert state.code_10 == '0402101900'


def test_l5_invalid_leaf_does_not_become_last_leaf():
    result = SubsubpositionResult(selected_code_10='0402109999', selected_code_8='04021099')
    state = CascadeState('Молоко')
    with pytest.raises(ValueError, match='not a leaf'):
        Level5Subsubposition()._finalize(
            state, result, [SimpleNamespace(code='0402101900', name='молоко')], '040210',
        )
    assert state.code_10 is None


def test_invalid_runner_up_cannot_hide_real_second_branch():
    shortlist = SubsubpositionShortlistResult(
        selected_code_8='04021011', candidates=[
            {'code_8': '04021099', 'confidence': 1},
            {'code_8': '04021019', 'confidence': 0.8},
        ],
    )
    assert _get_top_branches(shortlist, valid_branches={'04021011', '04021019'}) == [
        '04021011', '04021019',
    ]
    assert _get_top_branches(shortlist, max_branches=1) == ['04021011']


@pytest.mark.parametrize('level,field,wrong,correct', [
    (1, 'section', 'VII', 'XVI'),
    (2, 'group', '84', '85'),
    (3, 'position', '8509', '8516'),
])
async def test_upper_level_records_selected_candidate(monkeypatch, level, field, wrong, correct):
    module = importlib.import_module(f'feap.cascade.levels.level{level}')
    for name in vars(module):
        if name.startswith('get_'):
            monkeypatch.setattr(module, name, AsyncMock(return_value=[]))
    nodes = [SimpleNamespace(code=code, name=code) for code in [wrong, correct]]
    if level == 1:
        monkeypatch.setattr(module, 'get_sections', AsyncMock(return_value=nodes))
        handler = module.Level1Section()
    elif level == 2:
        monkeypatch.setattr(module, 'get_groups_by_section', AsyncMock(return_value=nodes))
        handler = module.Level2Group()
    else:
        monkeypatch.setattr(module, 'get_children', AsyncMock(return_value=nodes))
        monkeypatch.setattr(module, 'get_node_by_code', AsyncMock(
            return_value=SimpleNamespace(id=1)))
        handler = module.Level3Position()
    candidates = [SimpleNamespace(**{field: code}, reasoning=reason, confidence=confidence)
                  for code, reason, confidence in [
                      (wrong, 'Отвергнутый кандидат', 0), (correct, 'Выбранный кандидат', 1),
                  ]]
    result = SimpleNamespace(**{f'selected_{field}': correct}, candidates=candidates,
                             applied_rules=[], backtrack_trigger=None, missing_parameters=[])
    responses = [result] if level == 2 else [SimpleNamespace(shortlisted=[correct]), result]
    monkeypatch.setattr(module, 'classify', AsyncMock(side_effect=responses))
    state = CascadeState('Утюг')
    state.section, state.group = 'XVI', '85'
    state.product_card = {'original_description': 'Утюг', 'normalized_description': 'Утюг'}
    await handler.run(state, None)
    assert state.chain[-1].code == correct
    assert state.chain[-1].confidence == 1
    assert state.chain[-1].reasoning == 'Выбранный кандидат'
