"""Benchmark v2: case schema, v1 migration, per-case outcomes and their summary."""
import pytest

from benchmark.metrics.outcome import Outcome, case_outcome, summarize
from benchmark.metrics.report_v2 import render_markdown
from benchmark.metrics.stats import wilson
from benchmark.schema.case_v2 import (
    CaseV2,
    ClassificationGold,
    GoldLayers,
    PassportInput,
    Provenance,
    Sufficiency,
    Verification,
    is_v2_file,
    load_cases,
    load_dataset,
    save_cases,
    section_of,
)
from benchmark.schema.prediction import CasePrediction
from benchmark.schema.test_case import GroundTruth
from benchmark.schema.test_case import TestCase as V1Case
from benchmark.scripts.migrate_v1_to_v2 import gold_from_audit, migrate_case

BUDGET_402 = ("Error code: 402 - {'error': {'message': 'This request would exceed your "
              "available credits', 'metadata': {'reason': 'in_flight_budget_exhausted', "
              "'headers': {'Retry-After': '120'}}}}")


def case(cid='C1', code='8516400000', verification=Verification.UNVERIFIED,
         sufficiency=Sufficiency.UNKNOWN, acceptable=(), difficulty='easy'):
    return CaseV2(
        id=cid, track='passport', split='regression', tier='D',
        provenance=Provenance(kind='synthetic', reference='test'), difficulty=difficulty,
        input=PassportInput(description='Утюг электрический бытовой'),
        gold=GoldLayers(classification=ClassificationGold(
            code=code, acceptable=list(acceptable),
            verification=verification, sufficiency=sufficiency,
        )),
    )


def prediction(cid='C1', code='8516400000', status='completed', review=False, error=None):
    return CasePrediction(
        case_id=cid, status=status, predicted_code_10=code, predicted_confidence=0.9,
        error_message=error,
        raw_response={'result': {'requires_human_review': review, 'risk_flags': []}},
    )


# ── schema ──────────────────────────────────────────────────────────────────

def test_gold_code_has_heading_to_ten_digit_precision():
    for bad in ('85164', '8516400', '8516 40', '851640000a'):
        with pytest.raises(ValueError):
            case(code=bad)
    with pytest.raises(ValueError):
        case(acceptable=['85164'])
    assert case(code='8516').gold.classification.digits == 4


def test_old_files_with_code_10_still_load():
    data = case().model_dump(mode='json')
    data['gold']['classification']['code_10'] = data['gold']['classification'].pop('code')
    assert CaseV2.model_validate(data).gold.classification.code == '8516400000'


def test_partial_gold_accepts_any_code_inside_it():
    gold = case(code='2106').gold.classification
    assert gold.accepts('2106909809') and not gold.accepts('2103909000')
    assert not gold.accepts(None)
    outcome = case_outcome(case(code='2106'), prediction(code='2106909809'))
    assert outcome.outcome is Outcome.CORRECT and outcome.reason == 'prefix'


def test_dossier_track_is_rejected_until_phase_two():
    with pytest.raises(ValueError, match='phase 2'):
        CaseV2.model_validate({**case().model_dump(), 'track': 'dossier'})


def test_round_trip_and_duplicate_ids(tmp_path):
    path = tmp_path / 'cases.jsonl'
    save_cases([case('A'), case('B')], path)
    assert [c.id for c in load_cases(path)] == ['A', 'B']
    save_cases([case('A'), case('A')], path)
    with pytest.raises(ValueError, match='Duplicate'):
        load_cases(path)


def test_v2_files_load_into_the_v1_runner(tmp_path):
    path = tmp_path / 'cases.jsonl'
    save_cases([case('A'), case('B', code='2106')], path)
    assert is_v2_file(path)
    dataset = load_dataset(path)
    assert [c.id for c in dataset.cases] == ['A', 'B']
    assert dataset.cases[0].ground_truth.code_10 == '8516400000'
    assert dataset.cases[1].ground_truth.code_10 == '2106000000'  # padded for v1 only
    assert dataset.cases[1].difficulty.value == 'easy'


def test_section_of_group_boundaries():
    assert section_of('0409000000') == 'I'
    assert section_of('8501522001') == 'XVI'
    assert section_of('9701000000') == 'XXI'
    assert section_of('3923309090') == 'VII'


# ── migration of benchmark_100 and the v32 audit ────────────────────────────

def v1(cid='BENCH-M06', code='1602501000'):
    return V1Case(
        id=cid, product_description='Тушёнка говяжья стерилизованная в банках',
        ground_truth=GroundTruth.from_code10(code, 'IV'), source='Ручная разметка',
        trap_codes=['0210200000'],
    )


SOURCE = {'url': 'https://eec.eaeunion.org/x/ru.16_2022.pdf', 'pdf_pages_one_based': [2],
          'retrieved_on': '2026-09-21', 'sha256': 'abc'}


def test_unaudited_label_stays_unverified():
    gold = gold_from_audit(v1(), None)
    assert gold.verification is Verification.UNVERIFIED
    assert gold.sufficiency is Sufficiency.UNKNOWN
    assert gold.code == '1602501000' and gold.reviews == []


def test_correction_becomes_source_reviewed_with_history():
    gold = gold_from_audit(v1(), {
        'disposition': 'corrected', 'old_code_10': '1602501000',
        'replacement_code_10': '1602509501', 'reason': 'не сырое мясо', 'source': SOURCE,
    })
    assert gold.code == '1602509501'
    assert gold.verification is Verification.SOURCE_REVIEWED
    assert gold.reviews[0].previous_code == '1602501000'
    assert gold.reviews[0].sources[0].title == 'ЕТТ ЕАЭС, группа 16, PDF ЕЭК'


def test_quarantine_is_not_scored_and_names_missing_fact():
    gold = gold_from_audit(v1('BENCH-M04', '1006309809'), {
        'disposition': 'quarantined_exact10', 'old_code_10': '1006309809',
        'replacement_code_10': None, 'reason': 'нет отношения длины к ширине', 'source': SOURCE,
    })
    assert not gold.scored
    assert gold.sufficiency is Sufficiency.INSUFFICIENT
    assert gold.missing_facts == ['нет отношения длины к ширине']


def test_h30_keeps_both_decisions_and_is_disputed():
    gold = gold_from_audit(v1('BENCH-H30', '8518210000'), {
        'disposition': 'corrected', 'old_code_10': '8518210000',
        'replacement_code_10': '8518220009', 'reason': 'два динамика', 'source': SOURCE,
    })
    assert gold.verification is Verification.DISPUTED and not gold.scored
    assert [r.disposition for r in gold.reviews] == ['corrected', 'disputed']
    assert {c.code for c in gold.conditional} == {'8518220009', '8518309500'}


def test_migrated_case_keeps_v1_input_and_tags():
    old = v1().model_copy(update={'requires_clarification': True,
                                  'additional_info': {'жирность': '8.5%'}})
    new = migrate_case(old, None)
    assert new.id == old.id and new.tier.value == 'D' and new.split == 'regression'
    assert new.input.additional_info == {'жирность': '8.5%'}
    assert 'answer_in_additional_info' in new.tags


# ── outcomes ────────────────────────────────────────────────────────────────

def test_outcome_paths():
    insufficient = case(sufficiency=Sufficiency.INSUFFICIENT)
    sufficient = case(sufficiency=Sufficiency.SUFFICIENT)
    assert case_outcome(case(), None).reason == 'not_run'
    assert case_outcome(case(), prediction()).outcome is Outcome.CORRECT
    assert case_outcome(case(acceptable=['8516409000']),
                        prediction(code='8516409000')).reason == 'acceptable'
    wrong = prediction(code='8516900000')
    assert case_outcome(case(), wrong).outcome is Outcome.SILENT_ERROR
    flagged = case_outcome(case(), prediction(code='8516900000', review=True))
    assert flagged.outcome is Outcome.SAFE_STOP and flagged.reason == 'flagged_wrong'
    asked = prediction(code=None, status='needs_input')
    assert case_outcome(insufficient, asked).reason == 'question_justified'
    assert case_outcome(sufficient, asked).reason == 'question_unneeded'
    assert case_outcome(case(), asked).outcome is Outcome.SAFE_STOP


def test_failures_are_classified_and_stale_codes_ignored():
    budget = case_outcome(case(), prediction(status='error', error=BUDGET_402))
    assert budget.outcome is Outcome.FAILURE and budget.reason == 'budget'
    assert budget.predicted_code is None
    timeout = case_outcome(case(), prediction(status='error', error='Timeout after 300s'))
    assert timeout.reason == 'timeout'


def test_summary_excludes_unestablished_gold_and_measures_flags():
    cases = [
        case('ok'), case('silent'), case('caught'),
        case('quarantined', verification=Verification.QUARANTINED),
    ]
    preds = [
        prediction('ok', review=True), prediction('silent', code='8516900000'),
        prediction('caught', code='8516900000', review=True),
        prediction('quarantined', code='8516900000'),
    ]
    summary = summarize([case_outcome(c, p) for c, p in zip(cases, preds)])
    assert summary['n_scored'] == 3 and summary['not_scored'] == ['quarantined']
    assert summary['outcomes']['silent_error']['k'] == 1
    assert summary['flags']['flag_recall_on_wrong']['k'] == 1
    assert summary['flags']['false_alarm_on_correct'] == {
        'k': 1, 'n': 1, 'share': 1.0, 'ci95': pytest.approx([0.2065, 1.0], abs=1e-4),
    }
    assert summary['silent_errors'] == ['silent']


def test_wilson_interval_stays_in_unit_range():
    assert wilson(0, 10)[0] == 0.0
    assert wilson(10, 10)[1] == 1.0
    low, high = wilson(79, 88)
    assert low == pytest.approx(0.8166, abs=1e-3) and high == pytest.approx(0.9452, abs=1e-3)
    assert wilson(0, 0) is None


def test_report_of_a_partial_run_says_it_is_not_a_result():
    outcomes = [case_outcome(case('A'), prediction('A'))]
    summary = {**summarize(outcomes), 'run_id': 'smoke', 'cases_path': 'c.jsonl',
               'predictions_path': 'p.jsonl', 'partial': {'scored_cases': 1, 'planned_cases': 150}}
    manifest = {'cases': {'sha256': 'a' * 64}, 'predictions': {'sha256': 'b' * 64},
                'scored_at': '2026-10-18T00:00:00+00:00'}
    report = render_markdown(summary, outcomes, {}, manifest)
    assert 'Частичный прогон' in report and '1 из 150' in report
