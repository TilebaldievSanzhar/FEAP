"""Build the payment scenarios (benchmark/data/scenarios/calc_v1.jsonl).

Each scenario declares the rates read in official sources; the amounts are not
stored — the oracle derives them, so a reader can redo every number by hand.
Duty rates marked source_reviewed were read in the EEC tariff PDFs (local
copies in data/ett_pdfs, sha256 recorded) by the team, not by an expert.
Excise, VAT benefits and trade remedies come from the reference data on the
build date (snapshot_locked): the scenario then guards against silent changes,
it does not prove the value. Exchange rates are the NBKR rates for 15.10.2026
as stored by refsync.

Run:
    python -m benchmark.scripts.build_calc_scenarios
"""
from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal
from pathlib import Path

from benchmark.schema.calc_scenario import CalcScenario, Declared, DeclaredDuty, save_scenarios
from benchmark.schema.case_v2 import SourceRef
from feap.calc.schemas import CalcIn
from feap.dt.schemas import RemedyDecision

OUT_PATH = Path('benchmark/data/scenarios/calc_v1.jsonl')
DAY = dt.date(2026, 10, 15)
FX = {'USD': Decimal('87.45'), 'EUR': Decimal('97.8959')}

FX_SOURCE = SourceRef(title='НБКР: официальные курсы на 15.10.2026 — USD 87,45; EUR 97,8959 '
                            '(лента НБКР в exchange_rates)', retrieved_on=dt.date(2026, 10, 16))
VAT_SOURCE = SourceRef(title='НК КР: НДС при импорте 12% (VAT_RATE_DEFAULT)')
FEE_SOURCE = SourceRef(title='ПП КР № 79 от 13.02.2020, п. 38: 0,4% от таможенной стоимости, '
                             'не менее 5 и не более 2500 РП (РП = 100 сом)')


def ett_pdf(group: str, page: int, note: str = '') -> SourceRef:
    path = Path(f'data/ett_pdfs/ru.{group}.pdf')
    sha = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    return SourceRef(title=f'ЕТТ ЕАЭС, группа {group}, PDF ЕЭК ({path.as_posix()}){note}',
                     pages=[page], sha256=sha)


GROUP_85 = SourceRef(
    title='ЕТТ ЕАЭС, группа 85, PDF ЕЭК (ред. 05.07.2026): 8501 52 200 1 — «5» со сноской 63С '
          '(0% с 28.03.2022 по 30.09.2022, истекла); 8501 52 200 9 — «0»',
    url='https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.85_2022_05.07.2026.pdf',
    pages=[11],
    sha256='6d91d7ae3001af29623d74c2b758f3c8452f87716027378ee0889397b7806d3f',
)


def calc(code: str, price: str, **over) -> CalcIn:
    base = dict(hs_code=code, price=Decimal(price), currency='USD', incoterms='CPT',
                origin_country='CN', net_kg=Decimal('900'), gross_kg=Decimal('1000'),
                rate_date=DAY)
    base.update(over)
    return CalcIn(**base)


def declared(duty: DeclaredDuty, **over) -> Declared:
    return Declared(fx=FX, duty=duty, vat_pct=Decimal('12'), **over)


PCT_12 = DeclaredDuty(text='12%', pct=Decimal('12'))
ALUMINIUM = [ett_pdf('76', 3), FX_SOURCE, VAT_SOURCE, FEE_SOURCE]
BEEF_DUTY = DeclaredDuty(text='50%, но не менее 1 EUR за 1 кг', pct=Decimal('50'),
                         min_eur_per_kg=Decimal('1'))
BEEF_SOURCES = [
    ett_pdf('02', 9), FX_SOURCE, FEE_SOURCE,
    SourceRef(title='ПКМ КР № 816 от 22.12.2025, прил. 1: НДС 0% для 020110 '
                    '(vat_exemptions, источник pkm816_a1)'),
]
SHOES_DUTY = DeclaredDuty(text='0.75 EUR за 1 пар', eur_per_pair=Decimal('0.75'))
SHOES = [ett_pdf('64', 2), FX_SOURCE, VAT_SOURCE, FEE_SOURCE]
COOKWARE = [ett_pdf('76', 9), FX_SOURCE, VAT_SOURCE, FEE_SOURCE]
AD32 = SourceRef(title='Реестр мер защиты рынка ЕЭК: AD-32 «Алюминиевая посуда», Китай, 21,89% '
                       '(trade_remedies на дату сборки)')

SCENARIOS = [
    CalcScenario(
        id='CALC-01', title='Адвалорная пошлина 12%, CPT, доллары',
        checks='таможенная стоимость, пошлина, НДС, сбор 0,4%',
        calc=calc('7604210000', '10000'), declared=declared(PCT_12),
        expected_status='ready', sources=ALUMINIUM, verification='source_reviewed',
    ),
    CalcScenario(
        id='CALC-02', title='Нулевая пошлина, НДС от таможенной стоимости',
        checks='ставка 0% не превращается в «не рассчитано»; доп. единица шт',
        calc=calc('8501522009', '10000', qty=Decimal('50'), qty_unit='шт',
                  supp_qty=Decimal('50'), supp_unit='шт'),
        declared=declared(DeclaredDuty(text='0%', pct=Decimal('0'))),
        expected_status='ready', sources=[GROUP_85, FX_SOURCE, VAT_SOURCE, FEE_SOURCE],
        verification='source_reviewed',
    ),
    CalcScenario(
        id='CALC-03', title='Ставка со сноской после окончания её срока',
        checks='временная ставка 0% по сноске 63С не применяется в 2026 году',
        calc=calc('8501522001', '10000', qty=Decimal('50'), qty_unit='шт',
                  supp_qty=Decimal('50'), supp_unit='шт'),
        declared=declared(DeclaredDuty(text='5%', pct=Decimal('5'))),
        expected_status='ready', sources=[GROUP_85, FX_SOURCE, VAT_SOURCE, FEE_SOURCE],
        verification='source_reviewed',
    ),
    CalcScenario(
        id='CALC-04', title='FOB: фрахт и страховка до границы ЕАЭС',
        checks='доначисления к цене по Incoterms (метод 1)',
        calc=calc('7604210000', '10000', incoterms='FOB', freight_to_border=Decimal('800'),
                  insurance=Decimal('30')),
        declared=declared(PCT_12), expected_status='ready', sources=ALUMINIUM,
        verification='source_reviewed',
    ),
    CalcScenario(
        id='CALC-05', title='CIF: вычет фрахта после границы',
        checks='документально выделенный фрахт после границы уменьшает таможенную стоимость',
        calc=calc('7604210000', '10000', incoterms='CIF', freight_beyond_border=Decimal('400')),
        declared=declared(PCT_12), expected_status='ready', sources=ALUMINIUM,
        verification='source_reviewed',
    ),
    CalcScenario(
        id='CALC-06', title='Контракт в евро, страна без мер защиты рынка',
        checks='курс EUR; мера AD-32 действует только для Китая',
        calc=calc('7615101000', '10000', currency='EUR', origin_country='TR'),
        declared=declared(DeclaredDuty(text='9%', pct=Decimal('9'))),
        expected_status='ready', sources=COOKWARE, verification='source_reviewed',
    ),
    CalcScenario(
        id='CALC-07', title='Сбор: минимум 5 РП',
        checks='0,4% от малой стоимости меньше 500 сом',
        calc=calc('7604210000', '500'), declared=declared(PCT_12),
        expected_status='ready', sources=ALUMINIUM, verification='source_reviewed',
    ),
    CalcScenario(
        id='CALC-08', title='Сбор: максимум 2500 РП',
        checks='0,4% от большой стоимости больше 250 000 сом',
        calc=calc('7604210000', '8000000', net_kg=Decimal('720000'), gross_kg=Decimal('800000')),
        declared=declared(PCT_12), expected_status='ready', sources=ALUMINIUM,
        verification='source_reviewed',
    ),
    CalcScenario(
        id='CALC-09', title='«Не менее»: адвалорная часть больше',
        checks='50%, но не менее 1 евро за 1 кг — дорогая партия; НДС 0% по ПКМ № 816',
        calc=calc('0201100008', '10000'),
        declared=Declared(fx=FX, duty=BEEF_DUTY, vat_pct=Decimal('0')),
        expected_status='ready', sources=BEEF_SOURCES, verification='snapshot_locked',
        note='Ставка пошлины сверена по PDF ЕЭК; льгота НДС — из справочника, не перепроверена.',
    ),
    CalcScenario(
        id='CALC-10', title='«Не менее»: специфическая часть больше',
        checks='50%, но не менее 1 евро за 1 кг — дешёвая партия',
        calc=calc('0201100008', '1000'),
        declared=Declared(fx=FX, duty=BEEF_DUTY, vat_pct=Decimal('0')),
        expected_status='ready', sources=BEEF_SOURCES, verification='snapshot_locked',
        note='Ставка пошлины сверена по PDF ЕЭК; льгота НДС — из справочника, не перепроверена.',
    ),
    CalcScenario(
        id='CALC-11', title='Специфическая пошлина за пару',
        checks='0,75 евро за 1 пару из доп. единицы',
        calc=calc('6401100000', '10000', qty=Decimal('1000'), qty_unit='пар',
                  supp_qty=Decimal('1000'), supp_unit='пар'),
        declared=declared(SHOES_DUTY), expected_status='ready', sources=SHOES,
        verification='source_reviewed',
    ),
    CalcScenario(
        id='CALC-12', title='Специфическая пошлина без доп. единицы',
        checks='нет количества пар — остановка с вопросом, не ноль',
        calc=calc('6401100000', '10000'), expected_status='needs_input',
        expected_missing=['количество пар'], sources=SHOES, verification='source_reviewed',
    ),
    CalcScenario(
        id='CALC-13', title='Акциз на вино из справочника акцизов',
        checks='акциз в сомах за литр входит в базу НДС',
        calc=calc('2204219800', '10000', origin_country='IT', qty=Decimal('1000'),
                  qty_unit='бут', supp_qty=Decimal('750'), supp_unit='л'),
        declared=declared(DeclaredDuty(text='12.5%', pct=Decimal('12.5')),
                          excise_som_per_litre=Decimal('100')),
        expected_status='ready',
        sources=[ett_pdf('22', 9), FX_SOURCE, VAT_SOURCE, FEE_SOURCE,
                 SourceRef(title='excise_rates: 2204 — 100 сом/литр (сид, не сверен с НК КР)')],
        verification='snapshot_locked',
        note='Ставка акциза взята из справочника продукта и не сверена с НК КР.',
    ),
    CalcScenario(
        id='CALC-14', title='Антидумпинговая пошлина применяется',
        checks='AD-32 для Китая по решению декларанта; база НДС без пошлин защиты рынка',
        calc=calc('7615101000', '10000',
                  remedy_decisions={'AD-32': RemedyDecision(decision='applies')}),
        declared=declared(DeclaredDuty(text='9%', pct=Decimal('9')),
                          antidumping_pct=Decimal('21.89')),
        expected_status='ready', sources=[*COOKWARE, AD32], verification='snapshot_locked',
        note='Ставка меры — из снимка реестра ЕЭК; DT_VAT_INCLUDE_REMEDY_DUTIES=false.',
    ),
    CalcScenario(
        id='CALC-15', title='Мера защиты рынка без решения декларанта',
        checks='совпадение кода и страны с AD-32 — вопрос, а не молчаливый расчёт',
        calc=calc('7615101000', '10000'), expected_status='needs_input',
        expected_missing=['AD-32'], sources=[*COOKWARE, AD32], verification='snapshot_locked',
    ),
]


def main() -> None:
    manifest = save_scenarios(
        SCENARIOS, OUT_PATH,
        purpose=('Payment stage on real reference data against an independent oracle. '
                 'Rates are declared by the team, not by an expert.'),
    )
    print(f'{len(SCENARIOS)} scenarios → {OUT_PATH}\nmanifest → {manifest}')


if __name__ == '__main__':
    main()
