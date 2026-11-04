"""Наименования ЕТТ из колонки PDF ЕЭК: строки без кода, переносы, индексы.

Фикстура ``names_sample.json`` — окна настоящих страниц PDF групп ЕЭК
(data/ett_pdfs, редакция 2022, скачаны 17.03.2026) с координатами фрагментов:
шапка таблицы и строки вокруг трудных мест. Синтетические страницы ниже
проверяют правила по одному.
"""

from __future__ import annotations

import json
from pathlib import Path

from feap.tariff.ett import Span
from feap.tariff.ett_names import parse_group_names

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ett"
SAMPLE = json.loads((FIXTURES / "names_sample.json").read_text(encoding="utf-8"))


def _real(key: str):
    v = SAMPLE[key]
    return parse_group_names([[Span(*s) for s in page] for page in v["spans"]], v["group"])


def _path(leaf: dict) -> list[str]:
    return [*leaf["parent_names"], leaf["name"]]


# ── Настоящие страницы ──────────────────────────────────────────────────────

def test_unnumbered_rows_tell_siblings_apart():
    """0401 20 990 0 стоит под «– – более 3 мас.%»; в TWS — под «не более 3 мас.%»."""
    r = _real("0401")
    assert _path(r.leaves["0401201900"]) == [
        "с содержанием жира более 1 мас.%, но не более 6 мас.%", "не более 3 мас.%", "прочие"]
    assert _path(r.leaves["0401209900"]) == [
        "с содержанием жира более 1 мас.%, но не более 6 мас.%", "более 3 мас.%", "прочие"]
    assert r.leaves["0401209101"]["parent_names"][1:] == [
        "более 3 мас.%", "в первичных упаковках нетто-объемом не более 2 л"]
    assert r.positions["0401"]["name"] == (
        "Молоко и сливки, несгущенные и без добавления сахара или других подслащивающих веществ")
    assert r.issues == []


def test_number_in_name_column_is_text_not_a_heading():
    """Перенос «…в товарной позиции» / «6201» — текст, а не новая позиция 6201."""
    r = _real("6210")
    assert r.subpositions["621020"]["full_name"] == "предметы одежды прочие, типа указанных в товарной позиции 6201"
    assert r.subpositions["621030"]["full_name"] == "предметы одежды прочие, типа указанных в товарной позиции 6202"
    assert "6201" not in r.positions and "6202" not in r.positions


def test_unnumbered_row_above_subheadings_is_their_parent():
    r = _real("8524")
    assert r.subpositions["852411"]["full_name"] == "без драйверов или цепей управления > на жидких кристаллах"
    assert r.positions["8524"]["name"] == "Модули с плоской дисплейной панелью, в том числе с сенсорным экраном"


def test_dash_count_decides_the_parent():
    """8524 11 002 4 (четыре тире) — сосед «для товаров товарной позиции 8512», не его потомок."""
    r = _real("852411")
    assert r.leaves["8524110023"]["parent_names"][-1] == "для товаров товарной позиции 8512"
    assert r.leaves["8524110024"]["parent_names"] == [
        "предназначенные для товаров товарных позиций 8508 – 8510, 8512, 8516, 8518, 8525 – 8528,"
        " 8535 – 8537 или 8548"]


def test_heading_given_by_its_only_leaf():
    r = _real("0409")
    assert r.positions["0409"]["name"] == "Мед натуральный"
    assert r.leaves["0409000000"] == {
        "subposition": "040900", "name": "Мед натуральный", "parent_names": [], "full_name": "Мед натуральный"}


def test_deleted_heading_does_not_extend_the_previous_leaf():
    """«[0503]» в колонке кода — исключённая позиция, а не продолжение «прочие»."""
    assert _real("0503").leaves["0502900000"]["name"] == "прочие"


def test_subgroup_heading_is_not_part_of_a_leaf():
    """Группа 28: «II. КИСЛОТЫ НЕОРГАНИЧЕСКИЕ…» между позициями."""
    r = _real("2805")
    assert r.leaves["2805409000"]["name"] == "прочая"
    assert r.positions["2806"]["name"] == "Хлорид водорода (кислота соляная); кислота хлорсульфоновая"


def test_footnote_and_indices_stay_with_their_word():
    assert _real("850152").leaves["8501529001"]["name"] == "для гражданских воздушных судов 5)"
    assert "при 20 °С более 887,6 кг/м3, но не более 994 кг/м3" in _real("2709").leaves["2709009001"]["name"]
    assert _real("1101").leaves["1101001101"]["name"].startswith(
        "с содержанием витамина В1 и его производных не менее 1,2 мг/кг")


def test_unnumbered_row_may_start_with_a_number():
    r = _real("2206")
    assert _path(r.leaves["2206005100"]) == [
        "прочие", "неигристые, в сосудах емкостью", "2 л или менее", "сидр и грушевый сидр"]
    assert r.leaves["2206008100"]["parent_names"][-1] == "более 2 л"


# ── Синтетические страницы: одно правило — один тест ────────────────────────

_HEADER = [
    Span(405.4, 96.4, 110.7, 12.96, 4, "Доп."),
    Span(452.6, 96.4, 110.7, 12.96, 4, "Ставка"),
    Span(447.1, 163.6, 177.9, 12.96, 4, "в долларах США)"),
]
_LEFT = 187.2  # левый край колонки наименования


def _page(*rows: tuple) -> list[Span]:
    """Строки страницы: (код или None, текст); перенос — ("", текст, глубина строки)."""
    spans = list(_HEADER)
    y = 180.0
    for code, text, *depth in rows:
        if code == "":  # перенос: отступ ~10 pt на уровень тире
            spans.append(Span(_LEFT + 10 * depth[0] + 10, y, y + 14, 13.0, 0, text))
        elif code and code.count(" ") == 3:  # десятизначный код и начало наименования — один фрагмент
            spans.append(Span(93.7, y, y + 14, 13.0, 0, f"{code} {text}"))
        else:
            if code:
                spans.append(Span(93.7, y, y + 14, 13.0, 0, code))
            spans.append(Span(_LEFT, y, y + 14, 13.0, 0, text))
        y += 15.0
    return spans


def test_numbered_and_unnumbered_parents_survive_leaf_and_sibling_boundaries():
    r = parse_group_names([_page(
        ("4407", "Лесоматериалы:"), (None, "– хвойные:"), ("4407 11", "– – из сосны:"),
        (None, "– – – прочие:"), (None, "– – – – обработанные строганием:"),
        ("4407 11 340", "– – – – – сосна обыкновенная:"), ("4407 11 340 1", "– – – – – – влажностью не более 22%"),
        (None, "– – – – прочие:"), ("4407 11 940", "– – – – – сосна обыкновенная:"),
        ("4407 11 940 1", "– – – – – – влажностью не более 22%"), ("4407 11 940 9", "– – – – – – прочие"),
        ("4408", "Листы для облицовки:"), ("4408 10", "– хвойные:"), ("4408 10 000 1", "– – прочие"),
    )], "44")
    assert r.leaves["4407113401"]["parent_names"] == [
        "хвойные", "из сосны", "прочие", "обработанные строганием", "сосна обыкновенная"]
    assert r.leaves["4407119401"]["parent_names"] == ["хвойные", "из сосны", "прочие", "прочие", "сосна обыкновенная"]
    assert r.leaves["4407119409"]["parent_names"] == r.leaves["4407119401"]["parent_names"]
    assert r.leaves["4408100001"]["parent_names"] == ["хвойные"]
    assert r.subpositions["440711"]["full_name"] == "хвойные > из сосны"
    assert r.issues == []


def test_wrapped_line_starting_with_a_dash_is_a_continuation():
    """«…позиций 8425» / «– 8430:» — перенос с отступом, а не строка без кода."""
    r = parse_group_names([_page(
        ("8431", "Части:"), ("8431 10", "– для машин товарных позиций 8425"), ("", "– 8430:", 1),
        ("8431 10 000 0", "– – прочие"),
    )], "84")
    assert r.subpositions["843110"]["full_name"] == "для машин товарных позиций 8425 – 8430"


def test_row_continues_on_the_next_page():
    first = _page(("0101", "Лошади:"), ("0101 21 000 0", "– – чистопородные племенные"))
    second = _page(("", "животные", 2), ("0101 29 100 0", "– – прочие"))
    r = parse_group_names([first, second], "01")
    assert r.leaves["0101210000"]["name"] == "чистопородные племенные животные"
    assert r.leaves["0101291000"]["name"] == "прочие"


def test_skipped_dash_level_is_reported():
    r = parse_group_names([_page(("0101", "Лошади:"), ("0101 21 000 0", "– – – чистопородные"))], "01")
    assert r.issues == ["0101210000: глубина 3 после 0"]
