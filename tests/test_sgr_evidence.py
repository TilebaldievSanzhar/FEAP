from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from feap.cascade.evidence import evidence_context, evidence_records, pause_for_evidence, source_spans, validate_evidence
from feap.cascade.levels.base import CascadeState
from feap.cascade.levels.level5 import Level5Subsubposition
from feap.cascade.selection import choice_model, classify_choice
from feap.config import settings
from feap.schemas.subposition import EvidenceSubpositionResult, SubpositionResult
from feap.schemas.subsubposition import EvidenceSubsubpositionResult

CHOICES = {'8501510001': 'мощностью не более 750 Вт: асинхронные',
           '8501510009': 'мощностью не более 750 Вт: прочие'}


def result(status='supported', ids=None):
    return EvidenceSubsubpositionResult(
        evidence_checks=[dict(code='8501510001', alternative_code='8501510009',
                              parameter='тип двигателя', source_ids=[1] if ids is None else ids,
                              comparison='тип двигателя', status=status)],
        selected_code_10='8501510001', selected_code_8='850151', confidence=0.95,
    )


def test_evidence_precedes_decision_and_preserves_prefix_validator():
    for model in (EvidenceSubpositionResult, EvidenceSubsubpositionResult):
        fields = list(model.model_fields)
        assert fields[0] == 'evidence_checks'
        assert fields.index('evidence_checks') < fields.index('candidates')
    assert result().selected_code_8 == '85015100'
    assert 'evidence_checks' not in SubpositionResult.model_fields
    bound = choice_model(EvidenceSubsubpositionResult, {'selected_code_10': CHOICES})
    assert bound.model_validate(result().model_dump()).selected_code_8 == '85015100'


@pytest.mark.parametrize('status,ids,match', [
    ('supported', [2], 'provided SOURCE'),
    ('supported', [0], 'provided SOURCE'),
    ('supported', [1, 1], 'distinct'),
    ('contradicted', [1], 'contradicted condition'),
])
def test_invalid_or_contradicted_evidence_rejected(status, ids, match):
    with pytest.raises(ValueError, match=match):
        validate_evidence(result(status, ids), decision_field='selected_code_10',
                          descriptions=CHOICES, source='Двигатель синхронный')


def test_unknown_specific_branch_blocks_residual_in_clarify_mode(monkeypatch):
    monkeypatch.setattr(settings, 'SGR_EVIDENCE_POLICY', 'clarify')
    r = result('unknown', [])
    r.selected_code_10 = '8501510009'
    validate_evidence(r, decision_field='selected_code_10', descriptions=CHOICES, source='Двигатель')
    state = CascadeState('Двигатель')
    state.subposition = '850151'
    Level5Subsubposition()._finalize(state, r, [], '850151')
    assert state.code_10 is None
    assert state.missing_parameters == ['тип двигателя']
    assert not state.chain


def test_unknown_best_guess_keeps_audit_and_caps_confidence(monkeypatch):
    monkeypatch.setattr(settings, 'SGR_EVIDENCE_POLICY', 'best_guess')
    r = result('unknown', [])
    validate_evidence(r, decision_field='selected_code_10', descriptions=CHOICES, source='Двигатель')
    state = CascadeState('Двигатель')
    leaves = [SimpleNamespace(code='8501510001', name='асинхронные')]
    Level5Subsubposition()._finalize(state, r, leaves, '850151')
    assert state.code_10 == '8501510001'
    assert state.chain[-1].confidence == 0.5
    assert state.chain[-1].evidence_checks[0]['status'] == 'unknown'


@pytest.mark.parametrize('status,ids', [('supported', []), ('contradicted', []), ('unknown', [1])])
def test_ungrounded_assertion_becomes_unknown_and_keeps_reported_status(status, ids):
    r = result(status, ids)
    validate_evidence(r, decision_field='selected_code_10', descriptions=CHOICES, source='Двигатель')
    record = evidence_records(r)[0]
    assert record['status'] == 'unknown'
    assert record['reported_status'] == status
    assert record['source_quote'] == ''
    assert record['grounding_issue']
    assert r.missing_parameters == ['тип двигателя']
    assert r.confidence == 0.5


async def test_bad_citation_uses_existing_bounded_repair():
    invalid, valid = result(ids=[99]), result()
    call = AsyncMock(side_effect=[invalid, valid])
    checked = await classify_choice(
        classify_fn=call, choices={'selected_code_10': CHOICES}, level=5,
        user_message='Двигатель асинхронный', output_model=EvidenceSubsubpositionResult,
        validate_result=lambda r: validate_evidence(r, decision_field='selected_code_10',
                                                   descriptions=CHOICES, source='Двигатель асинхронный'),
    )
    assert checked is valid
    assert call.await_count == 2


def test_references_resolve_to_verbatim_sources_and_alternatives_must_exist(monkeypatch):
    monkeypatch.setattr(settings, 'SGR_EVIDENCE_POLICY', 'best_guess')
    source = 'Двигатель асинхронный. Мощность 7.5 кВт; новый.'
    assert source_spans(source) == ['Двигатель асинхронный.', 'Мощность 7.5 кВт;', 'новый.']
    assert 'source_id' in evidence_context(source)
    r = result()
    validate_evidence(r, decision_field='selected_code_10', descriptions=CHOICES, source=source)
    record = evidence_records(r)[0]
    assert record['criterion_quote'] == CHOICES['8501510001']
    assert record['source_quote'] == 'Двигатель асинхронный.'
    r = result()
    r.evidence_checks[0].alternative_code = '9999999999'
    with pytest.raises(ValueError, match='outside'):
        validate_evidence(r, decision_field='selected_code_10', descriptions=CHOICES, source='асинхронный')


def test_known_parameter_does_not_pause(monkeypatch):
    monkeypatch.setattr(settings, 'SGR_EVIDENCE_POLICY', 'clarify')
    r = result()
    validate_evidence(r, decision_field='selected_code_10', descriptions=CHOICES, source='асинхронный')
    assert not pause_for_evidence(CascadeState('асинхронный'), r)


async def test_clarification_survives_session_and_answer_reaches_prompt(monkeypatch):
    import feap.cascade.orchestrator as module
    from feap.prompts.registry import render
    from feap.schemas.validation import ClassificationResult

    monkeypatch.setattr(settings, 'SGR_EVIDENCE_POLICY', 'clarify')
    stored = {}

    async def save(db, sid, value, org_id=None):
        stored[sid] = value

    async def load(db, sid):
        value = stored.get(sid)
        return None if value is None else SimpleNamespace(state=value, org_id=None)

    async def delete(db, sid):
        stored.pop(sid)

    async def leaf(state, db):
        state.subposition = '850151'
        if state.product_card is None:
            state.product_card = {'original_description': state.product_description}
        answered = 'асинхронный' in state.product_description
        r = result('supported' if answered else 'unknown', [2] if answered else [])
        validate_evidence(r, decision_field='selected_code_10', descriptions=CHOICES,
                          source=state.product_description)
        if answered:
            assert 'тип двигателя: асинхронный' in render(
                'context/product_card.jinja2', card=state.product_card)
            assert evidence_records(r)[0]['source_quote'] == 'тип двигателя: асинхронный'
        Level5Subsubposition()._finalize(state, r, [SimpleNamespace(code='8501510001', name='асинхронные')], '850151')
        return r

    monkeypatch.setattr(module, 'save_session', save)
    monkeypatch.setattr(module, 'load_session_row', load)
    monkeypatch.setattr(module, 'delete_session', delete)
    monkeypatch.setattr(module, 'LEVELS', [SimpleNamespace(level=5, run=leaf)])
    monkeypatch.setattr(module, 'validate_and_build_result', AsyncMock(
        return_value=ClassificationResult.model_construct(final_code='8501510001')))
    orchestrator = module.CascadeOrchestrator()
    initial = await orchestrator.classify('Двигатель', None)
    assert initial.status == 'needs_input'
    assert initial.partial.missing_parameters == ['тип двигателя']
    assert initial.partial.evidence_checks[0]['status'] == 'unknown'
    resumed = await orchestrator.resume(initial.session_id, {'тип двигателя': 'асинхронный'}, None)
    assert resumed.status == 'completed'
    assert resumed.result.final_code == '8501510001'
    assert not stored
