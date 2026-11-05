"""Статусы условий → сигналы S1–S7 → флаги политик P1–P3."""
from feap.decision.conditions import build_conditions
from feap.decision.contracts import ConditionKind, Method, Status
from feap.decision.signals import POLICIES, compute_signals, evaluate, policy_flags
from tests.decision.test_rows import tree

T, F, U = Status.T, Status.F, Status.U


def _own(cc, default, **by_text):
    """Собственные статусы семантических и числовых условий: по тексту строки, иначе ``default``."""
    own = {}
    for cid, c in cc.conditions.items():
        if c.kind in (ConditionKind.SEMANTIC, ConditionKind.NUMERIC):
            status = by_text.get(c.question or c.text, default)
            own[cid] = (status, Method.MODEL, None, [])
    return own


def _names(signals):
    return sorted({(s.name, s.strength) for s in signals})


def _m02(**answers):
    t = tree('0207')
    cc = build_conditions({'0207': t}, '0207147009', ['02071460', '020713'])
    path_texts = {cc.conditions[c].question for c in cc.chosen if cc.conditions[c].question}
    own = _own(cc, F, **{**{text: T for text in path_texts}, **answers})
    return cc, evaluate(cc, own)


def test_confirmed_specific_sibling_refutes_the_residual_s2():
    cc, statuses = _m02(**{'ножки и куски из них': T})
    other = cc.chosen[-2]  # «прочие» под «необваленные»
    assert statuses[other].residual is F and statuses[other].status is F
    signals = compute_signals(cc, statuses)
    assert ('S2', 'strong') in _names(signals)
    assert next(s for s in signals if s.name == 'S2').conditions[-1] == cc.alternatives['02071460'][-1]
    assert policy_flags(signals) == {'P1': True, 'P2': True, 'P3': True}


def test_regime_sibling_does_not_block_the_residual():
    cc, statuses = _m02()
    leaf = statuses[cc.chosen[-1]]  # «прочие» после строки квоты: квота не заявлена → F
    assert leaf.residual is T and leaf.status is T
    quota = statuses[cc.preceding[cc.chosen[-1]][0]]
    assert quota.status is F and quota.method is Method.PRESUMPTION


def test_unknown_sibling_leaves_the_residual_open_s5():
    cc, statuses = _m02(**{'ножки и куски из них': U})
    assert statuses[cc.chosen[-2]].residual is U
    signals = compute_signals(cc, statuses)
    assert _names(signals) == [('S5', 'weak')]
    assert policy_flags(signals) == {'P1': False, 'P2': False, 'P3': True}


def test_refuted_path_condition_s1_and_unknown_s4():
    cc, statuses = _m02(**{'части тушек и субпродукты, замороженные': F, 'необваленные': U})
    names = [name for name, _ in _names(compute_signals(cc, statuses))]
    assert 'S1' in names and 'S4' in names


def test_confirmed_alternative_s3_is_strong_when_the_chosen_path_is_not_clean():
    cc, statuses = _m02(**{'ножки и куски из них': T})
    s3 = [s for s in compute_signals(cc, statuses) if s.name == 'S3']
    assert [(s.strength, s.detail) for s in s3] == [('strong', 'подтверждена альтернатива 02071460')]


def test_two_confirmed_specific_siblings_s6_and_assortment():
    cc, statuses = _m02(**{'ножки и куски из них': T, 'грудки и куски из них': T})
    assert ('S6', 'strong') in _names(compute_signals(cc, statuses))  # строка пути — «прочие», она F
    clean_cc, clean = _m02()
    assert _names(compute_signals(clean_cc, clean, assortment=T)) == [('S6', 'strong')]


def test_confirmed_path_row_with_a_confirmed_sibling_is_the_opi3_zone():
    """«газоанализаторы» и «хроматографы» обе T, выбран один из них — S6 средний, вне P1."""
    t = tree('0207')
    cc = build_conditions({'0207': t}, '0207146009', [])
    own = _own(cc, F, **{cc.conditions[c].question: T for c in cc.chosen if cc.conditions[c].question})
    own.update({cid: (T, Method.MODEL, None, []) for cid, c in cc.conditions.items()
                if c.question == 'грудки и куски из них'})
    signals = compute_signals(cc, evaluate(cc, own))
    assert ('S6', 'medium') in _names(signals)
    assert policy_flags(signals) == {'P1': False, 'P2': True, 'P3': True}


def test_regime_on_the_chosen_path_always_asks_s7():
    t = tree('8501')
    cc = build_conditions({'8501': t}, '8501529001', [])  # «для гражданских воздушных судов 5)»
    statuses = evaluate(cc, _own(cc, T))
    signals = compute_signals(cc, statuses)
    assert ('S7', 'question') in _names(signals)
    assert 'S1' not in [s.name for s in signals]  # презумпция режима — не опровержение товара
    assert policy_flags(signals)['P1']


def test_policies_are_nested():
    assert POLICIES['P1'] < POLICIES['P2'] < POLICIES['P3']


def test_unchecked_preceding_siblings_count_as_unknown():
    t = tree('2204')
    widest = max((r for r in t.rows.values() if r.children), key=lambda r: len(r.children))
    leaf_row = t.rows[widest.children[-1]]
    while leaf_row.children:
        leaf_row = t.rows[leaf_row.children[-1]]
    cc = build_conditions({'2204': t}, leaf_row.leaves[0], [], cap=3)
    residual_rows = [c for c in cc.chosen if cc.omitted_preceding.get(c)]
    assert residual_rows, 'на широкой развилке «прочие» видит не всех соседей'
    statuses = evaluate(cc, _own(cc, F))
    assert all(statuses[c].residual is U for c in residual_rows)


def test_confirmed_heading_refutes_a_residual_heading_alternative():
    """H22: 9401 подтверждена — «Мебель прочая» (9403) не может быть подтверждённой альтернативой."""
    from tests.decision.test_conditions import _headings
    trees = {p: tree(p) for p in ('9401', '9403')}
    cc = build_conditions(trees, '9401710009', ['9403'], headings=_headings())
    statuses = evaluate(cc, _own(cc, T))
    assert statuses['9403'].own is T and statuses['9403'].residual is F and statuses['9403'].status is F
    assert 'S3' not in [s.name for s in compute_signals(cc, statuses)]
