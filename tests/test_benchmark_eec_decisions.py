"""EEC Collegium decisions: list page, Russian text choice, classification items."""
from benchmark.collectors.eec_decisions import (
    parse_items,
    parse_list,
    russian_file,
    uchet_paragraphs,
    uchet_url,
)
from benchmark.scripts.build_eec_sets import items, opens_leaf


def test_text_of_scanned_decisions_comes_from_the_republished_html():
    assert uchet_url('2020-01-28', '14') == 'https://zakon.uchet.kz/rus/docs/H20EK000014'
    page = ('<h1>О классификации</h1><section class="m-t" itemprop="articleBody"><article>'
            '<p id="z4">&nbsp; Коллегия <b>решила:</b></p>'
            '<p id="z5">&nbsp; 1. Стальной шкаф универсальный в соответствии с Основным правилом '
            'интерпретации Товарной номенклатуры внешнеэкономической деятельности 1 '
            'классифицируется в товарной позиции 9403 единой Товарной номенклатуры.</p>'
            '</article></section>')
    found = parse_items(uchet_paragraphs(page))
    assert [(i['item'], i['code']) for i in found] == [(1, '9403')]


HEADER ='<table><tr><td><b>№ п/п</b></td><td>Код</td><td>Номер и дата</td><td>Товар</td></tr>'


def row(n, code, number, date, title, extra=''):
    return (f'<tr><td><p>{n}.</p></td><td><p>{code}</p></td><td><p><a href="https://docs.eaeunion.org/'
            f'documents/1/{number}/">№ {number}<br> {date}</a></p></td><td><p>{title}{extra}</p></td></tr>')


def test_list_rows_decode_utf8_and_cp1251_and_read_status():
    page = (HEADER.encode('utf-8')
            + row(1, '3214 10', 394, '20.09.2010', 'Пена монтажная').encode('cp1251')
            + row(2, '9013 10 000 0', 107, '18.08.2026', 'Тепловизионный прицел').encode('utf-8')
            + row(3, '8705 90 900 1', 749, '16.08.2011', 'Машина подметальная',
                  ' (внесены изменения Решением № 155)').encode('utf-8')
            + b'</table>')
    rows = parse_list(page)
    assert [r['title'] for r in rows][:2] == ['Пена монтажная', 'Тепловизионный прицел']
    assert rows[0]['codes'] == ['321410'] and rows[1]['codes'] == ['9013100000']
    assert rows[1]['number'] == '107' and rows[1]['date'] == '2026-08-18'
    assert [r['status'] for r in rows] == ['in_force', 'in_force', 'amended']


def test_russian_docx_is_chosen_among_official_languages():
    landing = ('<a href="/a/x-arm.docx" target="_blank">Հայ</a>'
               '<a href="/a/Rashenne-ad.docx" target="_blank">Бел</a>'
               '<a href="/a/Reshenie-ot.pdf" target="_blank">Рус</a>'
               '<a href="/a/Reshenie-ot.docx" target="_blank">Рус docx</a>')
    assert russian_file(landing) == ('/a/Reshenie-ot.docx', 'docx')
    assert russian_file('<a href="/a/x-arm.docx">Հայ</a>') is None


def test_items_with_one_or_several_rules_and_a_heading_level_code():
    paragraphs = [
        'О классификации пищевого продукта',
        '1. Пищевой продукт «приправа», представляющий собой лимонный сок прямого отжима, '
        'в соответствии с Основным правилом интерпретации Товарной номенклатуры '
        'внешнеэкономической деятельности 1 классифицируется в товарной позиции 2009 единой '
        'Товарной номенклатуры.',
        '2. Тепловизионный прицел с модулем Wi-Fi, в соответствии с Основными правилами '
        'интерпретации Товарной номенклатуры внешнеэкономической деятельности 1 и 6 '
        'классифицируется в субпозиции 9013 10 000 0 единой Товарной номенклатуры.',
        '3. Настоящее Решение вступает в силу по истечении 30 календарных дней.',
    ]
    found = parse_items(paragraphs)
    assert [(i['item'], i['code'], i['rules']) for i in found] == [
        (1, '2009', '1'), (2, '9013100000', '1 и 6')]
    older = parse_items([
        '1. В соответствии с Основными правилами интерпретации Товарной номенклатуры '
        'внешнеэкономической деятельности 1, 3(б) и 6 в подсубпозиции 8474 20 000 3 единой '
        'Товарной номенклатуры внешнеэкономической деятельности Евразийского экономического '
        'союза классифицируется дробильно-сортировочная установка для гранита.',
    ])
    assert [(i['code'], i['rules'], i['description']) for i in older] == [
        ('8474200003', '1, 3(б) и 6', 'дробильно-сортировочная установка для гранита')]
    assert found[0]['description'].startswith('Пищевой продукт «приправа»')
    assert found[0]['level'] == 'товарной позиции'


def test_builder_keeps_items_in_force_with_codes_of_the_current_nomenclature():
    leaves = ['2009110000', '9013100000']
    assert opens_leaf('2009', leaves) and not opens_leaf('2010', leaves)
    item = {'item': 1, 'description': 'Сок', 'rules': '1', 'level': 'товарной позиции',
            'code': '2009'}
    decisions = [
        {'status': 'in_force', 'error': None, 'date': '2026-08-04', 'items': [item]},
        {'status': 'in_force', 'error': None, 'date': '2026-08-05', 'items': [item]},
        {'status': 'amended', 'error': None, 'date': '2026-08-04', 'items': [item]},
        {'status': 'in_force', 'error': None, 'date': '2015-01-01', 'items': [item]},
        {'status': 'in_force', 'error': None, 'date': '2026-08-04',
         'items': [{**item, 'code': '2010'}]},
    ]
    kept, dropped = items(decisions, leaves)
    assert len(kept) == 1
    assert dropped == {'duplicate item': 1, 'decision amended': 1, 'decided before 2017': 1,
                       'code not in current nomenclature': 1}


def test_item_wording_variants_of_older_decisions():
    from benchmark.scripts.build_eaeu_pd_sets import parse_rules

    one_paragraph = [
        'В соответствии с пунктом 7 статьи 52 Таможенного кодекса Таможенного союза Коллегия '
        'Евразийской экономической комиссии решил а: 1. Установка для ремонта скважин на шасси, '
        'в соответствии с Основными правилами интерпретации ТН ВЭД 1 и 6 классифицируется в '
        'подсубпозиции 8705 90 800 0 ТН ВЭД ЕАЭС. 2. Смазочный материал на минеральном масле, '
        'в соответствии с Основным правилом интерпретации ТН ВЭД 1 классифицируется в товарной '
        'позиции 3403 ТН ВЭД ЕАЭС. 3. Настоящее Решение вступает в силу.',
    ]
    assert [(i['item'], i['code']) for i in parse_items(one_paragraph)] == [
        (1, '8705908000'), (2, '3403')]

    unnumbered_and_split = [
        'В соответствии с абзацем первым пункта 1 статьи 22 ТК ЕАЭС Коллегия решила:',
        'Газохол, применяемый в качестве моторного топлива, являющийся смесью бензина,',
        'биоэтанола и присадок,в соответствии с Основным правилом интерпретации Товарной '
        'номенклатуры внешнеэкономической деятельности 1 классифицируется в товарной позиции 2710.',
        '2. Настоящее Решение вступает в силу по истечении 30 дней.',
    ]
    found = parse_items(unnumbered_and_split)
    assert [(i['item'], i['code']) for i in found] == [(1, '2710')]
    assert found[0]['description'].endswith('биоэтанола и присадок')

    with_note = parse_items([
        '1. Гидравлический перфоратор, в соответствии с Основным правилом интерпретации Товарной '
        'номенклатуры внешнеэкономической деятельности 1, примечанием 2 (а) к разделу XVI единой '
        'Товарной номенклатуры классифицируется в товарной позиции 8412 единой Товарной номенклатуры.',
    ])
    assert with_note[0]['code'] == '8412'
    assert parse_rules(f"ОПИ {with_note[0]['rules']}") == ['ОПИ 1']  # note 2 (а) is not ОПИ 2а
