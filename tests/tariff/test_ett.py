"""ЕТТ из сводной редакции ЕЭК: столбцы PDF, ставки, примечания, ставка на дату.

Фикстуры — настоящие данные ЕЭК, снятые 16.10.2026: текст «Примечаний к ЕТТ»
(ред. 08.10.2026) и фрагменты страниц PDF групп 20, 28, 57, 71 с координатами.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from feap.dt.duty_parser import DutyKind, parse_duty_rate
from feap.tariff import ett

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ett"
NOTES = ett.parse_notes((FIXTURES / "notes_2026-10-08.txt").read_text(encoding="utf-8"))
SPANS = json.loads((FIXTURES / "spans_sample.json").read_text(encoding="utf-8"))
D = dt.date


def _pages(group: str) -> list[list[ett.Span]]:
    return [[ett.Span(*s) for s in page] for page in SPANS[group]["pages"]]


# ── Примечания к ЕТТ ─────────────────────────────────────────────────────────

def test_all_notes_parsed():
    kinds = {}
    for n in NOTES.values():
        kinds[n.kind] = kinds.get(n.kind, 0) + 1
    assert kinds == {"rate": 91, "void": 34}


def test_note_with_explicit_period():
    [p] = NOTES["2С"].periods
    assert (p.rate, p.valid_from, p.valid_to, p.scope) == ("0%", D(2022, 1, 1), D(2023, 4, 30), "all")


def test_note_from_decision_entry_into_force():
    [p] = NOTES["123С"].periods
    assert p.rate == "0%" and p.valid_from is None and p.valid_to == D(2028, 10, 31)
    assert p.from_decision.startswith("Решения Совета Евразийской экономической комиссии от 9 июля 2026 г.")
    assert p.from_decision.endswith("№ 76")


def test_note_stepped_and_russia_only_parts():
    zero, ru = NOTES["64С"].periods
    assert (zero.rate, zero.valid_from, zero.valid_to, zero.scope) == ("0%", D(2022, 3, 28), D(2022, 9, 30), "all")
    assert (ru.rate, ru.valid_from, ru.scope) == ("8%", D(2022, 10, 1), "ru")
    first, second = NOTES["66С"].periods
    assert second.rate == "12%" and second.valid_from == D(2022, 10, 1) and second.valid_to == D(2023, 2, 28)
    assert second.scope == "all"


def test_note_combined_rates():
    [p] = NOTES["105С"].periods
    assert p.rate == "12%, но не менее 0.44 EUR за 1 кг" and p.valid_to == D(2026, 12, 31)
    assert NOTES["32С"].periods[0].rate == "10% плюс 0.17 EUR за 1 кг"
    assert NOTES["32С"].periods[0].scope == "ru"


def test_void_note():
    assert NOTES["1С"].kind == "void" and NOTES["1С"].periods == ()


def test_note_key_normalizes_latin_c():
    assert ett.note_key("124C)") == "124С" and ett.note_key("63С") == "63С"
    with pytest.raises(ett.EttParseError):
        ett.note_key("abc")


# ── Ставка: текст ЕТТ → формат tnved_tree.duty_rate ──────────────────────────

@pytest.mark.parametrize("raw,canonical,kind", [
    ("5", "5%", DutyKind.AD_VALOREM),
    ("5,5", "5.5%", DutyKind.AD_VALOREM),
    ("50, но не менее 1 евро за 1 кг", "50%, но не менее 1 EUR за 1 кг", DutyKind.COMBINED_MIN),
    ("5, но не менее 0,015 евро за  1 кг", "5%, но не менее 0.015 EUR за 1 кг", DutyKind.COMBINED_MIN),
    ("0,06 евро за 1 кг", "0.06 EUR за 1 кг", DutyKind.SPECIFIC),
    ("250 долларов США за 1000 кг", "250 USD за 1 т", DutyKind.SPECIFIC),
    ("203 доллара США за 1000 кг", "203 USD за 1 т", DutyKind.SPECIFIC),
    ("0,75 евро за 1 пару", "0.75 EUR за 1 пар", DutyKind.SPECIFIC),
    ("20, но не менее 0,8 евро за 1 см объема двигателя", "20%, но не менее 0.8 EUR за 1 см3, мл", DutyKind.COMBINED_MIN),
    ("3 евро за 1 см³ объема двигателя", "3 EUR за 1 см3, мл", DutyKind.SPECIFIC),
    ("4,5 плюс 0,02 евро за 1 кг", "4.5% плюс 0.02 EUR за 1 кг", DutyKind.COMBINED_PLUS),
    ("17, но не менее 1,7 евро за 1000 шт", "17%, но не менее 1.7 EUR за 1 1000 шт", DutyKind.COMBINED_MIN),
    ("0,38 евро за 1 м2", "0.38 EUR за 1 м2", DutyKind.SPECIFIC),
    ("15, либо 12,5, но не менее 0,6 евро за 1 см3 объема двигателя, в зависимости от того, "
     "какая из исчисленных сумм таможенной пошлины ниже",
     "15%, либо 12.5%, но не менее 0.6 EUR за 1 см3, мл, в зависимости от того, "
     "какая из исчисленных сумм таможенной пошлины ниже", DutyKind.ALTERNATIVE_MIN),
])
def test_canonical_rate_is_parseable(raw, canonical, kind):
    assert ett.canonical_rate(raw) == canonical
    assert parse_duty_rate(canonical).kind is kind


def test_unknown_rate_text_kept_for_manual_review():
    assert ett.canonical_rate("по особой схеме") == "по особой схеме"
    assert ett.canonical_rate("") == ""


# ── PDF группы (настоящие координаты) ────────────────────────────────────────

def test_group_28_rates_and_note_markers():
    rows = ett.parse_group(_pages("28"), "28")
    assert rows["2815200000"].rate == "5.5%" and rows["2815200000"].unit == "кг KOH"
    assert rows["2816100000"].rate == "5%" and rows["2816100000"].notes == ("63С",)
    assert rows["2823000000"].rate == "5%" and rows["2823000000"].notes == ("123С",)
    assert rows["2818200000"].rate == "0%"
    # Строки без кода (заголовки позиций) не превращаются в листья.
    assert all(len(code) == 10 for code in rows)


def test_unit_exponent_on_first_row_of_page():
    rows = ett.parse_group(_pages("57"), "57")
    assert rows["5702100000"].rate == "0.38 EUR за 1 м2"
    assert rows["5702100000"].rate_raw == "0,38 евро за 1 м2"


def test_plus_marked_code_and_note_above_first_row():
    rows = ett.parse_group(_pages("71"), "71")
    assert rows["7112920000"].rate == "15%" and rows["7112920000"].notes == ("111С",)


def test_intro_notes_page_without_table_header_is_skipped():
    rows = ett.parse_group(_pages("20"), "20")
    # На вводной странице группы коды есть только в тексте примечаний.
    assert rows["2008501100"].rate == "11%"
    assert "2008 70 110 0" not in rows["2008501100"].rate_raw


# ── Ставка на дату ───────────────────────────────────────────────────────────

V = D(2026, 8, 17)


def _eff(day, notes=("123С",), version=V):
    return ett.effective_rate(base_rate="5%", note_ids=notes, version=version, notes=NOTES, day=day)


def test_temporary_rate_from_decision_starts_at_edition():
    assert _eff(D(2026, 10, 16)).rate == "0%"
    eff = _eff(D(2026, 10, 16))
    assert eff.source == "ett_note" and eff.note_id == "123С" and eff.valid_to == D(2028, 10, 31)
    assert _eff(D(2028, 11, 1)).rate == "5%"   # срок истёк — базовая ставка
    assert _eff(D(2026, 8, 1)) is None         # раньше редакции — неизвестно


def test_expired_and_russia_only_notes_give_base_rate():
    assert _eff(D(2026, 10, 16), notes=("2С",), version=ett.BASE_EDITION).rate == "5%"
    assert _eff(D(2026, 10, 16), notes=("9С",), version=ett.BASE_EDITION).rate == "5%"
    assert _eff(D(2026, 10, 16), notes=("1С",), version=ett.BASE_EDITION).source == "ett_base"


def test_missing_note_is_an_issue():
    eff = _eff(D(2026, 10, 16), notes=("999С",))
    assert eff.issue and "999С" in eff.issue


def test_resolve_duty_paths():
    rows = {"2823000000": ett.StoredRow("5%", ("123С",), V)}
    groups = {"28": V}
    kw = dict(rows=rows, notes=NOTES, group_versions=groups, day=D(2026, 10, 16))
    temp = ett.resolve_duty("2823000000", fallback_rate="0%", **kw)
    assert temp.rate == "0%" and temp.trace["base_rate"] == "5%" and temp.issue is None
    removed = ett.resolve_duty("2830990000", fallback_rate="5%", **kw)
    assert removed.issue and "отсутствует в действующей редакции" in removed.issue
    other_group = ett.resolve_duty("8703221091", fallback_rate="15%", **kw)
    assert other_group.rate == "15%" and other_group.trace["source"] == "fallback" and other_group.issue is None
    before = ett.resolve_duty("2830990000", fallback_rate="5%", **{**kw, "day": D(2026, 3, 1)})
    assert before.issue is None and before.rate == "5%"


def test_diff_editions():
    old = {"1": ett.StoredRow("0%", (), None), "2": ett.StoredRow("5%", (), None),
           "3": ett.StoredRow("5%", (), None)}
    new = {"1": ett.StoredRow("5%", (), V), "2": ett.StoredRow("5%", ("123С",), V),
           "4": ett.StoredRow("7%", (), V)}
    changes = {c.code: c for c in ett.diff_editions(old, new, old_notes=NOTES, new_notes=NOTES,
                                                    day=D(2026, 10, 16))}
    assert (changes["1"].change, changes["1"].before, changes["1"].after) == ("rate", "0%", "5%")
    assert changes["2"].after == "0%" and "123С" in changes["2"].detail
    assert changes["3"].change == "removed" and changes["4"].change == "added"


# ── Страница ЕЭК ─────────────────────────────────────────────────────────────

PAGE = """
<p>(в ред. решений Коллегии ЕЭК от 04.10.2021 № 132,&nbsp;от 09.07.2026 № 76)</p>
<a href="/upload/files/catr/ett/ru.01_2022_25.04.2022.pdf">01</a>
<a href="/upload/files/catr/ett/ru.02_2022.pdf">02</a>
<a href="/comission/department/catr/ett/ru.2022/ru.28_2022_17.08.2026.pdf">28</a>
<a href="/comission/department/catr/ett/ru.2022/%D0%9F%D1%80%D0%B8%D0%BC%D0%B5%D1%87%D0%B0%D0%BD%D0%B8%D1%8F%20%D0%BA%20%D0%95%D0%A2%D0%A2_08.02.2024.pdf">old</a>
<a href="/comission/department/catr/ett/ru.2022/%D0%9F%D1%80%D0%B8%D0%BC%D0%B5%D1%87%D0%B0%D0%BD%D0%B8%D1%8F%20%D0%BA%20%D0%95%D0%A2%D0%A2_08.10.2026.pdf">new</a>
"""


def test_parse_page_groups_versions_notes_and_decisions():
    page = ett.parse_page(PAGE, "https://eec.eaeunion.org/comission/department/catr/ett/")
    assert set(page.groups) == {"01", "02", "28"}
    assert page.groups["28"].version == D(2026, 8, 17)
    assert page.groups["02"].version is None
    assert page.groups["28"].url == ("https://eec.eaeunion.org/comission/department/catr/ett/"
                                     "ru.2022/ru.28_2022_17.08.2026.pdf")
    assert page.notes is not None and page.notes.version == D(2026, 10, 8)
    assert page.decisions == ("04.10.2021 № 132", "09.07.2026 № 76")
