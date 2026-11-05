"""Holdout: закрытые наборы заморожены, пулы подбора с ними не пересекаются."""
import json

import pytest

from benchmark.holdout import (
    CASES_DIR,
    DEV_SETS,
    HOLDOUT_SETS,
    Holdout,
    HoldoutLeak,
    load_pool,
    shingles,
    source_of,
)
from benchmark.schema.case_v2 import CaseV2, load_cases, save_cases

UUID = '0aa115d0-5f1f-40ed-a5ea-7fa72c1bf425'
OTHER = '11111111-2222-3333-4444-555555555555'
TEXT = ('Перфоратор представляет собой ударную дрель и является ручным инструментом со встроенным '
        'электрическим двигателем переменного тока, предназначен для сверления отверстий в бетоне')


def case(cid: str, code: str, text: str, *, record: str | None = None, split: str = 'dev') -> CaseV2:
    kind = 'preliminary_decision' if record else 'synthetic'
    reference = f'Сборник (ЕС 999): запись {record}, таможенный орган — РОССИЯ' if record else 'ручная'
    return CaseV2.model_validate({
        'id': cid, 'track': 'passport', 'split': split, 'tier': 'A',
        'provenance': {'kind': kind, 'reference': reference},
        'input': {'description': text},
        'gold': {'classification': {'code': code, 'verification': 'official', 'sufficiency': 'unknown'}},
    })


@pytest.fixture
def holdout() -> Holdout:
    return Holdout({
        'eaeu_pd_test400': [case('PD-RU-0aa115d0', '8467219900', TEXT, record=UUID, split='test')],
        'eaeu_pd_test400_short': [case('PD-RU-0aa115d0-S', '8467219900', 'Перфоратор.', record=UUID,
                                       split='test')],
        'eec_decisions_test': [],
    })


def test_short_version_is_the_same_record():
    full = case('PD-RU-0aa115d0', '8467219900', TEXT, record=UUID)
    short = case('PD-RU-0aa115d0-S', '8467219900', 'Перфоратор.', record=UUID)
    assert source_of(full) == source_of(short) == f'pd:{UUID}'


def test_calibration_drops_record_code_and_near_duplicate(holdout):
    edited = TEXT.replace('в бетоне', 'в кирпиче и бетоне, мощность 800 Вт')
    pool = holdout.pool([
        case('same-record', '8467219900', 'Другой текст о том же решении', record=UUID),
        case('same-code', '8467219900', 'Дрель-шуруповёрт аккумуляторная', record=OTHER),
        case('edited-copy', '8467299000', edited),
        case('clean', '8516310000', 'Фен для волос бытовой электрический'),
    ], 'calibration')
    assert [c.id for c in pool.cases] == ['clean']
    assert [(leak.case_id, leak.kind) for leak in pool.excluded] == [
        ('same-record', 'record'), ('same-code', 'code'), ('edited-copy', 'text')]


def test_replay_keeps_records_that_only_share_a_code(holdout):
    pool = holdout.pool([
        case('same-code', '8467219900', 'Дрель-шуруповёрт аккумуляторная', record=OTHER),
        case('copy', '8467219900', TEXT + '.'),
    ], 'replay')
    assert [c.id for c in pool.cases] == ['same-code']
    assert pool.summary() == {'text': 1}


def test_sibling_on_the_same_template_is_not_a_duplicate():
    """Тот же заявитель, другой товар: рис круглозёрный против басмати (Жаккар 0,28)."""
    sibling = Holdout({'eaeu_pd_test400': [case('H', '1006309809', (
        'Товар не является готовым пищевым продуктом. Состав: рис басмати, морковь сушеная, лук '
        'репчатый сушеный, паприка сушеная, томаты сушеные, куркума порошок, кориандр порошок, '
        'чеснок сушеный гранулы. Основным компонентом товара является рис полностью обрушенный, '
        'полированный, непропаренный, неглазированный'), split='test')]})
    assert sibling.near_duplicate(
        'Товар не является готовым пищевым продуктом. Состав: рис круглозерный, морковь сушеная, лук '
        'репчатый сушеный, грибы шампиньоны сушеные, барбарис сушеный, кумин порошок, чеснок '
        'сушеный, перец черный молотый. Основным компонентом товара является рис короткозерный, '
        'неполированный, неглазированный, полностью обрушенный') is None


def test_check_fails_on_any_overlap(holdout):
    clean = [case('clean', '8516310000', 'Фен для волос бытовой электрический')]
    assert holdout.check(clean, 'calibration') == clean
    with pytest.raises(HoldoutLeak, match='same-code: code → 8467219900'):
        holdout.check([*clean, case('same-code', '8467219900', 'Дрель', record=OTHER)], 'calibration')


def test_pool_file_with_a_holdout_case_is_refused(holdout, tmp_path):
    path = tmp_path / 'pool.jsonl'
    save_cases([case('leak', '8467219900', TEXT, record=UUID)], path)
    with pytest.raises(HoldoutLeak):
        load_pool(path, 'replay', holdout)


def test_shingles_ignore_case_punctuation_and_yo():
    assert shingles('Ёлка, Новогодняя  искусственная!') == shingles('елка новогодняя искусственная')


# ── Настоящие наборы ────────────────────────────────────────────────────────

def test_frozen_holdout_matches_the_committed_sets():
    holdout = Holdout.load()
    frozen = json.loads((CASES_DIR / 'holdout.json').read_text(encoding='utf-8'))
    assert set(frozen['sets']) == set(HOLDOUT_SETS)
    assert len(holdout.sources) == 467 and len(holdout.codes10) == 404


@pytest.mark.parametrize('name', DEV_SETS)
def test_dev_sets_share_no_record_or_text_with_holdout(name):
    holdout = Holdout.load()
    cases = load_cases(CASES_DIR / f'{name}.jsonl')
    assert holdout.check(cases, 'replay') == cases


def test_dev150_for_thresholds_loses_two_codes_of_eec_decisions():
    holdout = Holdout.load()
    pool = holdout.pool(load_cases(CASES_DIR / 'eaeu_pd_dev150.jsonl'), 'calibration')
    assert len(pool.cases) == 148
    assert sorted(leak.holdout for leak in pool.excluded) == ['3304990000', '9503007000']
