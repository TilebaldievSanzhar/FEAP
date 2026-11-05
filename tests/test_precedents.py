"""Прецеденты: поиск похожих решений и их место в L3 каскада."""
from types import SimpleNamespace

import pytest

import feap.cascade.levels.level3 as level3
import feap.precedents as precedents
from feap.cascade.levels.base import CascadeState
from feap.precedents import Precedent, PrecedentIndex, excluded_ids, terms
from feap.schemas.common import DecisionChainEntry
from feap.schemas.position import PositionCandidate, PositionResult, ShortlistResult

ROWS = [
    ('a', '7315119000', 'Цепь роликовая из черного металла для эскалатора', 'KZ'),
    ('b', '8431310000', 'Части лифтов и эскалаторов: направляющая, кронштейн', 'RU'),
    ('c', '9107000000', 'Реле времени с часовым механизмом для включения нагрузки', 'BY'),
]


def test_tokens_are_crude_stems_and_numbers():
    assert terms('Цепи роликовые, 12,5 мм; ЁЛКА и на') == ['цепи', 'ролико', '12', '5', 'елка']


def test_search_ranks_the_most_similar_decision_first():
    index = PrecedentIndex(ROWS)
    found = index.search('Цепь прижимная эскалатора, пластинчатая, из черного металла', k=2)
    assert [p.id for p in found] == ['a', 'b'] and found[0].code == '7315119000'
    assert index.search('совсем другое', k=3) == [] and len(index) == 3


def test_excluded_ids_come_from_a_file(tmp_path):
    path = tmp_path / 'hide.txt'
    path.write_text('a\n\nc\n', encoding='utf-8')
    assert excluded_ids(path) == {'a', 'c'} and excluded_ids('') == set()


async def test_index_is_built_once_from_the_table_without_hidden_rows(monkeypatch, tmp_path):
    hide = tmp_path / 'hide.txt'
    hide.write_text('a\n', encoding='utf-8')
    monkeypatch.setattr('feap.config.settings.PRECEDENTS_EXCLUDE_PATH', str(hide))
    calls = []

    class Db:
        async def execute(self, query):
            calls.append(query)
            return SimpleNamespace(all=lambda: ROWS)

    precedents.reset()
    try:
        first = await precedents.search(Db(), 'цепь эскалатора', 3)
        await precedents.search(Db(), 'реле времени', 3)
    finally:
        precedents.reset()
    assert len(calls) == 1 and 'a' not in {p.id for p in first}


# ── L3: позиции прецедентов среди кандидатов ───────────────────────────────

def _p(code, country='KZ', description='Товар'):
    return Precedent(id=code, code=code, description=description, country=country, score=1.0)


def test_precedent_headings_skip_known_excluded_and_repeated():
    found = [_p('8431310000'), _p('7315119000'), _p('7315200000'), _p('9107000000'), _p('9405100000')]
    assert level3.precedent_headings(found, known={'8431'}, excluded_positions={'9405'},
                                     excluded_groups={'91'}) == ['7315']


def test_precedent_evidence_is_short_and_formatted():
    text = 'Д' * 500
    [block] = level3.precedent_evidence([_p('7315119000', description=text)], 5)
    assert block['code'] == '7315 11 900 0' and block['description'].endswith('…')
    assert len(block['description']) == level3.PRECEDENT_CHARS + 1 and '(KZ)' in block['source']


def test_reroute_rewrites_section_and_group_of_the_chain():
    state = CascadeState('Цепь эскалатора')
    state.section, state.group = 'XVI', '84'
    state.chain = [DecisionChainEntry(level=1, code='XVI', name='Машины', reasoning='', rules_applied=[],
                                      confidence=0.9),
                   DecisionChainEntry(level=2, code='84', name='Реакторы', reasoning='', rules_applied=[],
                                      confidence=0.9)]
    level3.reroute(state, section='XV', section_name='Металлы', group='73', group_name='Изделия из черных',
                   position='7315', confidence=0.8, rules=['ОПИ 1'])
    assert (state.section, state.group) == ('XV', '73')
    assert [(e.level, e.code) for e in state.chain] == [(1, 'XV'), (2, '73')]
    assert 'раздел XVI, группа 84' in state.chain[0].reasoning


def _node(code, name, section='XVI', node_id=1):
    return SimpleNamespace(id=node_id, code=code, name=name, section_roman=section, level=len(code) // 2)


NODES = {
    '84': _node('84', 'Реакторы ядерные, котлы, оборудование', node_id=84),
    '73': _node('73', 'Изделия из черных металлов', section='XV', node_id=73),
    'XV': _node('XV', 'Недрагоценные металлы', section='XV'),
    '8431': _node('8431', 'Части, предназначенные исключительно для машин 8425 – 8430'),
    '8479': _node('8479', 'Машины, имеющие индивидуальные функции'),
    '7315': _node('7315', 'Цепи и их части, из черных металлов', section='XV'),
}


def _wire(monkeypatch, *, enabled: bool, chosen: str):
    seen = {}

    async def node(db, code):
        return NODES.get(code)

    async def children(db, node_id):
        return [NODES['8431'], NODES['8479']]

    async def notes(db, scope_type, scope_code):
        seen.setdefault('notes', []).append((scope_type, scope_code))
        return [SimpleNamespace(scope_type=scope_type, scope_code=scope_code, note_number='1', note_type='general',
                                note_text=f'Примечание {scope_code}')]

    async def explanations(db, codes):
        seen['explained'] = list(codes)
        return []

    async def opi(db, level):
        return []

    async def search(db, text, k):
        seen['searched'] = (text, k)
        return [_p('7315119000'), _p('8431310000')]

    async def classify(*, output_model, user_message, **kwargs):
        if output_model.__name__ == 'ShortlistResult':
            return ShortlistResult(shortlisted=['8431'], reasoning='части')
        seen['choices'] = output_model.model_fields['selected_position'].annotation
        seen['prompt'] = user_message
        return PositionResult(candidates=[PositionCandidate(position=chosen, position_text='', reasoning='цепь',
                                                            opi_applied='ОПИ 1', confidence=0.8)],
                              selected_position=chosen, applied_rules=['ОПИ 1'])

    for name, fn in (('get_node_by_code', node), ('get_children', children), ('get_notes', notes),
                     ('get_explanations_by_codes', explanations), ('get_opi_rules', opi),
                     ('search_precedents', search), ('classify', classify)):
        monkeypatch.setattr(level3, name, fn)
    monkeypatch.setattr(level3.settings, 'CASCADE_PRECEDENTS', enabled)
    return seen


def _state():
    state = CascadeState('Цепь прижимная эскалатора, пластинчатая, из черного металла')
    state.product_card = {'original_description': state.product_description}
    state.section, state.group = 'XVI', '84'
    state.chain = [DecisionChainEntry(level=1, code='XVI', name='Машины', reasoning='', rules_applied=[],
                                      confidence=0.9),
                   DecisionChainEntry(level=2, code='84', name='Реакторы', reasoning='', rules_applied=[],
                                      confidence=0.9)]
    return state


async def test_without_the_flag_level3_is_unchanged(monkeypatch):
    seen = _wire(monkeypatch, enabled=False, chosen='8431')
    state = _state()
    await level3.Level3Position().run(state, db=None)
    assert 'searched' not in seen and seen['explained'] == ['8431']
    assert 'ПРЕЦЕДЕНТЫ' not in seen['prompt'] and state.precedent_codes == []
    assert state.position == '8431' and state.group == '84'


async def test_precedent_heading_of_another_section_can_be_chosen(monkeypatch):
    seen = _wire(monkeypatch, enabled=True, chosen='7315')
    state = _state()
    await level3.Level3Position().run(state, db=None)
    assert seen['explained'] == ['8431', '7315']
    assert ('group', '73') in seen['notes'] and ('section', 'XV') in seen['notes'] and ('section', 'XVI') in seen['notes']
    assert 'ПРЕЦЕДЕНТЫ КЛАССИФИКАЦИИ' in seen['prompt'] and '7315 11 900 0' in seen['prompt']
    assert (state.section, state.group, state.position) == ('XV', '73', '7315')
    assert [(e.level, e.code) for e in state.chain] == [(1, 'XV'), (2, '73'), (3, '7315')]
    assert state.chain[2].name == 'Цепи и их части, из черных металлов'
    assert state.precedent_codes == ['7315119000', '8431310000']


async def test_the_cascade_may_keep_its_own_group(monkeypatch):
    _wire(monkeypatch, enabled=True, chosen='8431')
    state = _state()
    await level3.Level3Position().run(state, db=None)
    assert (state.section, state.group, state.position) == ('XVI', '84', '8431')
    assert [e.code for e in state.chain] == ['XVI', '84', '8431']


def test_state_keeps_precedents_through_a_session():
    state = _state()
    state.precedent_codes = ['7315119000']
    assert CascadeState.from_dict(state.to_dict()).precedent_codes == ['7315119000']


@pytest.mark.parametrize('codes, final, match', [(['7315119000'], '7315119000', True), ([], '7315119000', False)])
async def test_result_reports_precedents(monkeypatch, codes, final, match):
    from feap.cascade import validator
    state = _state()
    state.code_10, state.precedent_codes = final, codes

    async def leaf(db, code):
        return SimpleNamespace(name='Цепи', duty_rate='5%')
    monkeypatch.setattr(validator, 'get_leaf_by_code', leaf)
    result = await validator.validate_and_build_result(state, db=None)
    assert result.precedent_match is match and result.precedent_codes == codes
