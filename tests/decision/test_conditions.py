"""Какие условия проверяются для кода каскада и как типизируются строки ЕТТ."""
import pytest

from feap.decision.conditions import build_conditions, classify, nearest, split_footnote
from feap.decision.contracts import ConditionKind
from tests.decision.test_rows import tree

K = ConditionKind


@pytest.mark.parametrize('text, kind, footnote', [
    ('для гражданских воздушных судов 5)', K.REGIME, '5'),
    ('для промышленной сборки моторных транспортных средств товарных позиций 8701 – 8705, их узлов и '
     'агрегатов 5)', K.REGIME, '5'),
    ('в порядке, указанном в дополнительном примечании Евразийского экономического союза 4 к группе 02',
     K.REGIME, None),
    ('прочие', K.RESIDUAL, None),
    ('прочие 6)', K.RESIDUAL, '6'),
    ('индюшачьи или гусиные 6)', K.SEMANTIC, '6'),
])
def test_regime_residual_and_footnotes(text, kind, footnote):
    fields = classify(text)
    assert fields['kind'] is kind and fields['footnote'] == footnote


def test_brackets_are_not_footnotes():
    assert split_footnote('соль метилфосфоновой кислоты и (аминоиминометил)мочевины (1: 1)')[1] is None
    assert split_footnote('содержащей сероводород (H2 S) 1)') == ('содержащей сероводород (H2 S)', '1')


@pytest.mark.parametrize('text, question', [
    ('креветки прочие', 'креветки'),
    ('мебель для сидения с металлическим каркасом прочая', 'мебель для сидения с металлическим каркасом'),
    ('лосось тихоокеанский прочий (Oncorhynchus gorbuscha)', 'лосось тихоокеанский (Oncorhynchus gorbuscha)'),
    ('прочие отруба, необваленные', 'отруба, необваленные'),
])
def test_residual_with_its_own_qualifier(text, question):
    fields = classify(text)
    assert fields['residual'] and fields['kind'] is K.SEMANTIC and fields['question'] == question


def test_numeric_residual_and_enumerations():
    numeric = classify('прочие, с содержанием жира более 1 мас.%')
    assert numeric['residual'] and numeric['kind'] is K.NUMERIC and numeric['quantity'] == 'с содержанием жира'
    for text in ('верблюды и прочие животные семейства верблюдовых (Camelidae)',
                 'печень, икра, молоки и прочие пищевые рыбные субпродукты',
                 'с добавлением сахара или других подслащивающих веществ, но без прочих ингредиентов',
                 'не армированные или не комбинированные иным способом с прочими материалами',
                 'средства химические контрацептивные на основе гормонов, прочих соединений товарной позиции 2937',
                 'вагоны железнодорожные, оборудованные помимо прочего дизель-генераторной установкой'):
        assert not classify(text)['residual'], text


@pytest.mark.parametrize('text, question', [
    ('из прочих текстильных материалов', 'из текстильных материалов'),
    ('соки прочих цитрусовых', 'соки цитрусовых'),
    ('филе прочей рыбы, свежее или охлажденное', 'филе рыбы, свежее или охлажденное'),
    ('для прочих целей', 'для целей'),
    ('сок из одного вида любых других фруктов, орехов или овощей', 'сок из одного вида фруктов, орехов или овощей'),
])
def test_residual_in_oblique_cases(text, question):
    fields = classify(text)
    assert fields['residual'] and fields['question'] == question


def test_references_and_numbers_are_marked():
    fields = classify('предназначенные для товаров товарной позиции 8413, 8415, 8418, 8421, 8423, 8476 или 8486')
    assert fields['kind'] is K.SEMANTIC and fields['interpretive'] and fields['has_number']
    heading = classify('Двигатели и генераторы электрические', heading=True)
    assert heading['kind'] is K.SEMANTIC and heading['question'] == 'Двигатели и генераторы электрические'


# ── Кейс M02: «прочие» вместо «ножки и куски из них» ───────────────────────

def _m02():
    t = tree('0207')
    alternatives = ['0207', '020713', '020714', '02071460', '02071470', '0207146001', '0207146009',
                    '0207147001', '0207147009']
    return t, build_conditions({'0207': t}, '0207147009', alternatives)


def test_chosen_path_and_its_forks():
    t, cc = _m02()
    assert [cc.conditions[c].text for c in cc.chosen][-3:] == ['необваленные', 'прочие', 'прочие']
    fork = next(f for f in cc.forks if f.row == t.node('02071470').id)
    assert [cc.conditions[c].text for c in fork.considered] == [
        'половины или четвертины', 'крылья целые, с тонким концом или без него',
        'спинки, шейки, спинки вместе с шейкой, гузки и тонкие концы крыльев', 'грудки и куски из них',
        'ножки и куски из них', 'прочие',
    ] and fork.omitted == 0


def test_residual_sees_its_preceding_siblings():
    t, cc = _m02()
    other = t.node('02071470').id
    assert [cc.conditions[c].text for c in cc.preceding[other]][-1] == 'ножки и куски из них'
    assert len(cc.preceding[other]) == 5
    leaf = t.leaf('0207147009').id  # «прочие» после строки квоты
    assert [cc.conditions[c].kind for c in cc.preceding[leaf]] == [K.REGIME]


def test_alternatives_from_the_trace():
    t, cc = _m02()
    assert '0207' not in cc.alternatives and '02071470' not in cc.alternatives  # это сам путь каскада
    assert cc.alternatives['02071460'][-1] == t.node('02071460').id
    assert cc.alternatives['020713'][-1] == t.node('020713').id
    assert cc.alternatives['0207146009'][-1] == t.leaf('0207146009').id


def test_unknown_alternative_is_noted_not_fatal():
    t = tree('0207')
    cc = build_conditions({'0207': t}, '0207147009', ['7604', '0207999999'])
    assert cc.alternatives == {} and len(cc.notes) == 2


def test_wide_fork_keeps_the_nearest_siblings():
    t = tree('2204')
    widest = max((r for r in t.rows.values() if r.children), key=lambda r: len(r.children))
    assert len(widest.children) > 15
    siblings = [t.rows[c] for c in widest.children]
    row = siblings[-1]
    kept = nearest(siblings, row, 15)
    assert len(kept) == 15 and row in kept and kept == siblings[-15:]


def test_chosen_code_must_be_a_leaf():
    t = tree('0207')
    with pytest.raises(ValueError, match='не лист'):
        build_conditions({'0207': t}, '0207140000', [])
    with pytest.raises(ValueError, match='Нет строк'):
        build_conditions({'0207': t}, '8501522001', [])


# ── «прочие» в тексте товарной позиции ─────────────────────────────────────

def _headings():
    from tests.decision.test_rows import SAMPLE
    return {group: [tuple(h) for h in rows] for group, rows in SAMPLE['headings'].items()}


def test_residual_heading_excludes_the_preceding_headings_of_its_group():
    """H22: «Мебель прочая и ее части» (9403) идёт после 9401 и 9402."""
    trees = {p: tree(p) for p in ('9401', '9403', '9404')}
    cc = build_conditions(trees, '9401710009', ['9403', '9404'], headings=_headings())
    furniture = cc.conditions['9403']
    assert furniture.residual and furniture.question == 'Мебель и ее части'
    assert cc.preceding['9403'] == ['9401', '9402'] and cc.omitted_preceding['9403'] == 0
    assert not cc.conditions['9404'].residual  # «… и аналогичные изделия» — не остаток
    assert cc.conditions['9402'].context == []


def test_without_group_headings_the_heading_text_is_a_plain_condition():
    trees = {p: tree(p) for p in ('9401', '9403')}
    cc = build_conditions(trees, '9401710009', ['9403'])
    assert not cc.conditions['9403'].residual and cc.conditions['9403'].question == 'Мебель прочая и ее части'
    assert '9403' not in cc.preceding


def test_enumerated_others_in_a_heading_are_not_a_residual():
    fields = classify('Провода изолированные, кабели и прочие изолированные электрические проводники', heading=True)
    assert not fields['residual']
    fields = classify('Изделия прочие из пластмасс и изделия из прочих материалов товарных позиций 3901 – 3914',
                      heading=True)
    assert fields['residual'] and fields['question'].startswith('Изделия из пластмасс')


def test_not_elsewhere_specified_heading_is_a_residual_and_sees_the_chosen_heading():
    """M34: двигатель 8501 и альтернатива 8543 «… в другом месте данной группы не поименованные»."""
    fields = classify('Машины электрические и аппаратура, имеющие индивидуальные функции, в другом месте данной '
                      'группы не поименованные или не включенные', heading=True)
    assert fields['residual'] and fields['question'] == 'Машины электрические и аппаратура, имеющие индивидуальные функции'
    trees = {p: tree(p) for p in ('8501', '8543')}
    cc = build_conditions(trees, '8501522001', ['8543'], headings=_headings())
    assert cc.preceding['8543'][0] == '8501' and len(cc.preceding['8543']) == 16  # 15 ближайших + позиция каскада
    assert cc.omitted_preceding['8543'] == 41 - 16  # 8501 проверяется, остальные до окна — нет


def test_heading_text_duplicated_as_a_row_is_dropped():
    """3209 90 000 0 назван по TWS: путь начинается с текста самой позиции."""
    t = tree('3209')
    assert [r.text for r in t.siblings(t.leaf('3209900000').id)] == [
        'на основе акриловых или виниловых полимеров', 'прочие']
