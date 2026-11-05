"""Один кейс целиком: условия → вопросы → ответы → статусы → сигналы. Исполнитель поддельный."""
from decimal import Decimal

from feap.decision.backend import ASSORTMENT_ID
from feap.decision.contracts import CallRecord, DecisionAnswer, EvidenceFact, Status
from feap.decision.verifier import QUESTIONS_VERSION, extraction_requests, verify_case
from tests.decision.test_rows import tree

T, F, U = Status.T, Status.F, Status.U


class FakeBackend:
    """Отвечает по тексту условия; на остальное — ``default``. Ссылается на первый фрагмент."""

    name = 'fake'
    model = 'fake'

    def __init__(self, labels: dict[str, str], default: str = 'contradicted'):
        self.labels = labels
        self.default = default
        self.asked: list = []

    async def answer(self, questions, spans):
        self.asked = questions
        answers = []
        for q in questions:
            label = self.labels.get(ASSORTMENT_ID if q.id == ASSORTMENT_ID else q.text, self.default)
            answers.append(DecisionAnswer(question_id=q.id, label=label, cites=True,
                                          source_ids=[] if label == 'not_stated' else [1], call_id='c1'))
        return answers, [CallRecord(id='c1', model='fake', cost_usd=0.002, questions=[q.id for q in questions])]


class FakeExtractor:
    name = 'fake-extract'
    model = 'fake'

    def __init__(self, facts: dict[str, EvidenceFact | None]):
        self.facts = facts
        self.requests: list = []

    async def extract(self, requests, spans):
        self.requests += requests
        return {r.key: self.facts.get(r.quantity) for r in requests}, [
            CallRecord(id='x1', model='fake', cost_usd=0.001, questions=[r.key for r in requests])]


def _flags_and_signals(trace):
    return trace.flags, sorted({s.name for s in trace.signals})


M02 = 'Окорочка куриные замороженные (ножки с бедром), необваленные, в коробках по 15 кг. Кур домашних (Gallus domesticus).'
M02_TRUE = ['Мясо и пищевые субпродукты домашней птицы, указанной в товарной позиции 0105, свежие, охлажденные '
            'или замороженные', 'кур домашних (Gallus domesticus)', 'части тушек и субпродукты, замороженные',
            'части тушек', 'необваленные', 'ножки и куски из них']


async def test_m02_residual_chosen_over_a_confirmed_sibling_is_flagged():
    backend = FakeBackend({text: 'supported' for text in M02_TRUE})
    trace = await verify_case(
        case_id='BENCH-M02', description=M02, chosen_code='0207147009',
        alternatives=['0207', '020713', '020714', '02071460', '02071470'], trees={'0207': tree('0207')},
        backend=backend)
    assert trace.error is None and trace.questions_version == QUESTIONS_VERSION
    flags, names = _flags_and_signals(trace)
    assert 'S2' in names and 'S3' in names and flags == {'P1': True, 'P2': True, 'P3': True}
    assert trace.questions[-1].id == ASSORTMENT_ID
    texts = [q.text for q in trace.questions]
    assert len(texts) == len(set(texts)) or len({(tuple(q.context), q.text) for q in trace.questions}) == len(texts)
    assert trace.cost_usd == 0.002 and trace.spans[0].startswith('Окорочка')


async def test_m02_with_the_right_code_stays_silent():
    backend = FakeBackend({text: 'supported' for text in M02_TRUE})
    trace = await verify_case(
        case_id='BENCH-M02', description=M02, chosen_code='0207146009', alternatives=['02071470'],
        trees={'0207': tree('0207')}, backend=backend)
    assert trace.signals == [] and not any(trace.flags.values())


MOTOR = 'Электродвигатель асинхронный трёхфазный. Номинальная выходная мощность 3 кВт.'


async def test_motor_power_is_extracted_once_and_compared_in_code():
    backend = FakeBackend({'Двигатели и генераторы электрические (кроме электрогенераторных установок)': 'supported',
                           'двигатели переменного тока многофазные': 'supported', 'асинхронные': 'supported'})
    extractor = FakeExtractor({'номинальной выходной мощностью': EvidenceFact(
        attribute='p', value=Decimal('3'), unit='кВт', source_ids=[2])})
    trace = await verify_case(case_id='M34', description=MOTOR, chosen_code='8501522001', alternatives=['850153'],
                              trees={'8501': tree('8501')}, backend=backend, extractor=extractor)
    assert [r.quantity for r in extractor.requests] == ['номинальной выходной мощностью']
    statuses = {s.condition_id: s for s in trace.statuses}
    path = [statuses[c] for c in trace.chosen_path]
    assert [s.status for s in path] == [T, T, T, T, T]
    assert statuses['8501/1'].status is F  # «не более 37,5 Вт» при 3 кВт
    assert trace.signals == [] and trace.cost_usd == 0.003


async def test_kva_instead_of_kw_leaves_the_power_unknown():
    backend = FakeBackend({'двигатели переменного тока многофазные': 'supported', 'асинхронные': 'supported'},
                          default='contradicted')
    backend.labels['Двигатели и генераторы электрические (кроме электрогенераторных установок)'] = 'supported'
    extractor = FakeExtractor({'номинальной выходной мощностью': EvidenceFact(
        attribute='p', value=Decimal('3'), unit='кВА', source_ids=[2])})
    trace = await verify_case(case_id='kva', description='Двигатель асинхронный, 3 кВА.', chosen_code='8501522001',
                              alternatives=[], trees={'8501': tree('8501')}, backend=backend, extractor=extractor)
    s4 = next(s for s in trace.signals if s.name == 'S4')
    assert {c for c in s4.conditions} == {'8501/5.2', '8501/5.2.1'}
    assert trace.flags == {'P1': False, 'P2': True, 'P3': True}


async def test_cached_facts_are_not_extracted_again():
    cc_requests, _ = extraction_requests(
        __import__('feap.decision.conditions', fromlist=['build_conditions']).build_conditions(
            {'8501': tree('8501')}, '8501522001', []))
    facts = {r.key: EvidenceFact(attribute='p', value=Decimal('3'), unit='кВт', source_ids=[1]) for r in cc_requests}
    extractor = FakeExtractor({})
    trace = await verify_case(case_id='cached', description=MOTOR, chosen_code='8501522001', alternatives=[],
                              trees={'8501': tree('8501')}, backend=FakeBackend({}), extractor=extractor,
                              facts=facts)
    assert extractor.requests == [] and set(trace.facts) == set(facts)


async def test_no_extractor_means_unknown_numbers_not_refuted():
    trace = await verify_case(case_id='noext', description=MOTOR, chosen_code='8501522001', alternatives=[],
                              trees={'8501': tree('8501')}, backend=FakeBackend({}, default='supported'))
    statuses = {s.condition_id: s for s in trace.statuses}
    assert statuses['8501/5.2'].own is U and any('нет извлечения' in n for n in trace.notes)


async def test_a_code_that_is_not_a_leaf_is_an_error_not_a_flag():
    trace = await verify_case(case_id='bad', description=M02, chosen_code='0207140000', alternatives=[],
                              trees={'0207': tree('0207')}, backend=FakeBackend({}))
    assert trace.error and trace.signals == [] and trace.flags == {}


async def test_assortment_answer_raises_s6():
    backend = FakeBackend({**{text: 'supported' for text in M02_TRUE}, ASSORTMENT_ID: 'supported'})
    trace = await verify_case(case_id='mix', description=M02, chosen_code='0207146009', alternatives=[],
                              trees={'0207': tree('0207')}, backend=backend)
    assert [s.conditions for s in trace.signals if s.name == 'S6'] == [['assortment']]
