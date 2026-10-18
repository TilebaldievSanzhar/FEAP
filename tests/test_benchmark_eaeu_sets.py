"""Tier-A sets from the EAEU collection of preliminary decisions: parsing and sampling."""
import random
from collections import Counter

from benchmark.scripts.build_eaeu_pd_sets import parse_rules, pool, quotas, sample
from benchmark.scripts.build_short_variants import short_description


def test_short_variant_is_the_first_sentence_without_the_lead():
    assert short_description('Товар представляет собой комбинированную заклепку из стали. '
                             'Предназначен для соединения деталей.') == \
        'Комбинированную заклепку из стали.'
    # Too short a first sentence takes the next one.
    assert short_description('Таблетки. Состав: левоцетиризина дигидрохлорид 5 мг.') == \
        'Таблетки. Состав: левоцетиризина дигидрохлорид 5 мг.'
    long = 'Оборудование ' + 'для прокатки металла ' * 20
    cut = short_description(long)
    assert len(cut) <= 160 and not cut.endswith(' ') and long.startswith(cut)


def test_rules_are_read_without_the_numbers_of_notes_and_explanations():
    assert parse_rules('ОПИ 1 и 6') == ['ОПИ 1', 'ОПИ 6']
    assert parse_rules('В соответствии с ОПИ 1, 2 а) и 6') == ['ОПИ 1', 'ОПИ 2а', 'ОПИ 6']
    assert parse_rules('Основные правила интерпретации 1 (примечание 2 (б) к разделу XVI '
                       'ТН ВЭД ЕАЭС) и 6 ТН ВЭД ЕАЭС.') == ['ОПИ 1', 'ОПИ 6']
    assert parse_rules('В соответствии с ОПИ 1 (примечание 1 а) к группе 29), 5(б) и 6, общие '
                       'положения пояснений к группе 29') == ['ОПИ 1', 'ОПИ 5б', 'ОПИ 6']
    assert parse_rules('В соответствии с ОПИ 1 и 6, пояснения к подсубпозиции 1601 00 100 0') \
        == ['ОПИ 1', 'ОПИ 6']
    assert parse_rules('') == []


def record(rid, code, description, country='КАЗАХСТАН'):
    return {'id': rid, 'versionId': 'v', 'data': {
        'CodeTNVED': code, 'Description': description,
        'Country': {'name': country}, 'Justification': 'ОПИ 1 и 6'}}


TEXT = 'Установка для удаления окалины с поверхности заготовки водой под давлением более 250 бар'


def test_pool_keeps_current_leaves_and_one_record_per_description():
    leaves = {'8424309000', '8424899000'}
    rows, dropped = pool([
        record('a', '8424 30 900 0', TEXT),
        record('b', '8424309000', TEXT + '  '),            # same description again
        record('c', '8424309001', TEXT + ' X'),             # not a leaf
        record('d', '8424899000', 'коротко'),               # too short
        record('e', '8424899000', TEXT + ' и сушкой'),
        record('f', '8424309000', TEXT + ' и сушкой'),      # same text, other code
    ], leaves)
    assert [r['record']['id'] for r in rows] == ['a']
    assert dropped == {'duplicate description': 1, 'code not a current leaf': 1,
                       'description length': 1, 'same description, different codes': 2}


def test_quotas_keep_a_floor_and_follow_weights():
    plan = quotas(30, {'XVI': 40, 'XI': 20, 'XIV': 3}, {'XVI': 0.8, 'XI': 0.2, 'XIV': 0.1})
    assert sum(plan.values()) == 30
    assert plan['XIV'] == 3 and plan['XI'] >= 8 and plan['XVI'] > plan['XI']


def test_sample_limits_codes_and_subheadings_and_keeps_forced_cases():
    rows = [{'record': {'id': f'r{i}', 'data': {'Country': {'name': 'РОССИЯ'}}},
             'code': f'8424{i % 3:02d}{i:04d}', 'description': str(i), 'key': str(i)}
            for i in range(60)]
    forced = [{'record': {'id': 'kg', 'data': {'Country': {'name': 'КЫРГЫЗСТАН'}}},
               'code': '8424990000', 'description': 'kg', 'key': 'kg'}]
    picked = sample(rows, 5, random.Random(1), {'XVI': 1.0}, {'84': 1.0}, forced=forced,
                    exclude_codes={rows[0]['code']})
    codes = [r['code'] for r in picked]
    assert picked[0] is forced[0] and len(picked) == 5
    assert len(set(codes)) == len(codes) and rows[0]['code'] not in codes
    assert max(Counter(c[:6] for c in codes).values()) <= 2
