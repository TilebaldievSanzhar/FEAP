"""Наименования ТН ВЭД из PDF групп ЕЭК: строки таблицы и иерархия «тире».

PDF тот же, что разбирает ``ett.parse_group`` (ставки), — здесь колонка
наименования. Координаты разводят то, что поток текста смешивает:

- код стоит в левой колонке (``x0 < _CODE_X_MAX``); число в колонке
  наименования — это текст: «…типа указанных в товарной позиции» / «6201»;
- строка без кода начинается с тире у левого края колонки наименования,
  а перенос длинного наименования — с отступом;
- доп. единица и ставка правее границы колонок и в наименование не попадают.

Глубина строки — число ведущих тире (0 — уровень товарной позиции). Родитель —
ближайшая строка выше с меньшей глубиной в той же позиции. Путь листа — тексты
строк от первой под позицией до самого листа. Так сохраняются и безномерные
строки («– – более 3 мас.%»), и номерные промежуточные («0401 20 910»).

Модуль чистый: PyMuPDF нужен только ``ett.extract_spans``.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from feap.tariff.ett import _CODE_RE, _CODE_X_MAX, Span, _columns

#: Фрагменты одной визуальной строки: надстрочная сноска «5)» стоит чуть выше.
_LINE_TOLERANCE = 3.0
#: Мелкий шрифт — надстрочный или подстрочный индекс (м³, С₅, сноска «5)»).
_SMALL = 0.8
#: Строка без кода начинается у левого края колонки наименования. Перенос строки
#: с тире набран с отступом (~10 pt на уровень), перенос наименования позиции — без.
_LEFT_TOLERANCE = 2.5
_DASHES = re.compile(r"^((?:[–—]\s*)+)")
_FOOTNOTE = re.compile(r"^\d+\)$")
#: Заголовок подгруппы внутри таблицы: «II. КИСЛОТЫ НЕОРГАНИЧЕСКИЕ…» (группы 28, 29).
_HEADING = re.compile(r"^[IVXLC]+\.\s+[А-ЯЁA-Z]")
#: Курсив — отдельные фрагменты: «(» + «Gallus domesticus» + «)», «вида "» + «Pinus…»,
#: химические приставки «N-фенил-» + «п» + «-фенилендиамин».
_SPACE_BEFORE = re.compile(r"\s+([).,;:\]])")
_SPACE_AFTER = re.compile(r"([(\[])\s+")
_QUOTE_OPEN = re.compile(r'(^|[\s(])"\s+(?=\S)')
_QUOTE_CLOSE = re.compile(r'(?<=\S)\s+"(?=$|[\s,.;:)])')
_ITALIC_PREFIX = re.compile(r"(^|[\s(\[-])(\w{1,2})\s+-(?=\w)")
_AFTER_HYPHEN = re.compile(r"(?<=\w-)\s+(?=\w{1,2}(?:-|\]|\)))")


def _tidy(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = _SPACE_AFTER.sub(r"\1", _SPACE_BEFORE.sub(r"\1", text))
    text = _QUOTE_CLOSE.sub('"', _QUOTE_OPEN.sub(r'\1"', text))
    text = _AFTER_HYPHEN.sub("", _ITALIC_PREFIX.sub(r"\1\2-", text))
    return text.strip()


@dataclass
class Row:
    """Строка таблицы ЕТТ: код (или None для строки без кода), глубина, текст."""

    code: str | None
    depth: int
    text: str
    page: int


@dataclass
class GroupNames:
    """Наименования одной группы и замечания разбора."""

    positions: dict[str, dict] = field(default_factory=dict)
    subpositions: dict[str, dict] = field(default_factory=dict)
    leaves: dict[str, dict] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)


def split_dashes(text: str) -> tuple[int, str]:
    """«– – более 3 мас.%:» → (2, «более 3 мас.%:»)."""
    m = _DASHES.match(text)
    if not m:
        return 0, text.strip()
    return m.group(1).count("–") + m.group(1).count("—"), text[m.end():].strip()


def _lines(spans: list[Span]) -> list[list[Span]]:
    """Фрагменты → визуальные строки сверху вниз, внутри строки — слева направо.

    Индекс мелким шрифтом относится к ближайшей по высоте строке обычного шрифта:
    подстрочный «1» у «витамина В» стоит на 5 pt ниже её верха, надстрочный «3»
    в «кг/м³» — на 0,6 pt выше верха своей строки и на 14 pt ниже предыдущей.
    """
    size = max((s.size for s in spans), default=0.0)
    lines: list[tuple[float, list[Span]]] = []
    for s in sorted((s for s in spans if s.size >= size * _SMALL), key=lambda s: (s.y0, s.x0)):
        if lines and abs(s.y0 - lines[-1][0]) <= _LINE_TOLERANCE:
            lines[-1][1].append(s)
        else:
            lines.append((s.y0, [s]))
    for s in (s for s in spans if s.size < size * _SMALL):
        nearest = min(lines, key=lambda line: abs(line[0] - s.y0), default=None)
        if nearest is not None and abs(nearest[0] - s.y0) <= 2 * _LINE_TOLERANCE:
            nearest[1].append(s)
        else:
            lines.append((s.y0, [s]))
    lines.sort(key=lambda line: line[0])
    return [sorted(members, key=lambda s: s.x0) for _, members in lines]


def _line_text(spans: list[Span], first: str | None = None) -> str:
    """Текст строки: индексы — к предыдущему слову (м3, С5), сноска «5)» — через пробел.

    ``first`` заменяет текст первого фрагмента (у строки с кодом — текст после кода).
    """
    size = max((s.size for s in spans), default=0.0)
    text = ""
    for i, s in enumerate(spans):
        part = (first if i == 0 and first is not None else s.text).strip()
        if not part:
            continue
        attach = s.size < size * _SMALL and not _FOOTNOTE.match(part)
        text = text + part if attach or not text else f"{text} {part}"
    return text


def _join(parts: list[str]) -> str:
    text = ""
    for part in (p.strip() for p in parts):
        if not part:
            continue
        # «нетто-» + «объемом»: перенос составного слова, дефис остаётся.
        text = text + part if text.endswith("-") or not text else f"{text} {part}"
    return text


def _left_edge(pages: list[list[Span]]) -> float | None:
    """Левый край колонки наименования: с него начинаются строки с тире."""
    xs: Counter[float] = Counter()
    for spans in pages:
        cols = _columns(spans)
        if cols is None:
            continue
        bottom, unit_x, _ = cols
        for s in spans:
            if s.y0 > bottom - 1 and _CODE_X_MAX <= s.x0 < unit_x and split_dashes(s.text)[0]:
                xs[round(s.x0)] += 1
    return float(min(xs)) if xs else None


def table_rows(pages: list[list[Span]], group: str) -> list[Row]:
    """Фрагменты страниц группы → строки таблицы в порядке документа."""
    left = _left_edge(pages)
    rows: list[Row] = []
    parts: list[list[str]] = []  # текст строк, пока идут переносы
    open_row = False  # следующий перенос продолжает последнюю строку
    for page_no, spans in enumerate(pages, 1):
        cols = _columns(spans)
        if cols is None:
            continue  # вводные примечания: коды там внутри текста
        bottom, unit_x, _ = cols
        body = [s for s in spans if s.y0 > bottom - 1 and s.x0 < unit_x]
        for line in _lines(body):
            if len(line) > 1 and line[0].text.strip() in ("+", "*") and line[0].x0 < _CODE_X_MAX:
                line = line[1:]  # пометка ЕТТ отдельным фрагментом перед кодом
            head = line[0]
            if head.x0 < _CODE_X_MAX:
                raw = head.text.replace("\xa0", " ")
                m = _CODE_RE.match(raw)
                digits = "".join(g for g in m.groups() if g) if m else ""
                if m and digits.startswith(group):
                    first = _line_text(line, raw[m.end():])
                    rows.append(Row(digits, split_dashes(first)[0], "", page_no))
                    parts.append([first])
                    open_row = True
                else:
                    open_row = False  # «[0503]» — исключённая позиция, не текст листа
                continue
            text = _line_text(line)
            depth, rest = split_dashes(text)
            at_left = left is not None and abs(head.x0 - left) <= _LEFT_TOLERANCE
            # Строка без кода может начинаться с числа: «– – – 2 л или менее»;
            # перенос, начинающийся с тире («– 8430»), стоит правее края.
            if depth > 0 and rest and at_left:
                rows.append(Row(None, depth, "", page_no))
                parts.append([text])
                open_row = True
            elif open_row and (not at_left or rows[-1].depth == 0) and not _HEADING.match(text):
                parts[-1].append(text)
            else:
                # У левого края без тире после строки с тире — не перенос, а
                # заголовок подгруппы и т. п.: до следующей строки таблицы.
                open_row = False
    for row, texts in zip(rows, parts):
        depth, text = split_dashes(_join(texts))
        row.depth = depth
        row.text = _tidy(text).rstrip(":").strip()
    return rows


def _subposition(position: str, parents: list[str], text: str) -> dict:
    return {
        "position": position,
        "own_name": text,
        "virtual_parents": list(parents),
        "full_name": " > ".join([*parents, text]),
    }


def build_names(rows: list[Row], group: str) -> GroupNames:
    """Строки таблицы → позиции, субпозиции и листья с полным путём."""
    out = GroupNames()
    stack: list[Row] = []
    position: str | None = None
    for row in rows:
        if row.code and row.code[:4] != position:
            position, stack = row.code[:4], []
        if position is None:
            continue
        if row.depth == 0:
            # Уровень позиции: «0401 Молоко…», «0205 00 Мясо лошадей…», «0409 00 000 0 Мед…».
            stack = []
            out.positions.setdefault(position, {"name": row.text, "group": group})
            if row.code and len(row.code) >= 6:
                out.subpositions.setdefault(row.code[:6], _subposition(position, [], row.text))
            if row.code and len(row.code) == 10:
                out.leaves[row.code] = {
                    "subposition": row.code[:6], "name": row.text,
                    "parent_names": [], "full_name": row.text,
                }
            continue
        while stack and stack[-1].depth >= row.depth:
            stack.pop()
        parent_depth = stack[-1].depth if stack else 0  # 0 — сама позиция
        if row.depth > parent_depth + 1:
            out.issues.append(f"{row.code or position}: глубина {row.depth} после {parent_depth}")
        parents = [r.text for r in stack]
        if row.code and len(row.code) == 10:
            foreign = [r.code for r in stack if r.code and not row.code.startswith(r.code)]
            if foreign:
                out.issues.append(f"{row.code}: чужие номерные родители {foreign}")
            out.leaves[row.code] = {
                "subposition": row.code[:6], "name": row.text,
                "parent_names": parents, "full_name": " > ".join([*parents, row.text]),
            }
            out.subpositions.setdefault(row.code[:6], _subposition(position, parents, row.text))
            continue
        if row.code and len(row.code) >= 6:
            out.subpositions.setdefault(row.code[:6], _subposition(position, parents, row.text))
        stack.append(row)
    return out


def parse_group_names(pages: list[list[Span]], group: str) -> GroupNames:
    """Фрагменты PDF группы → наименования (см. ``table_rows`` и ``build_names``)."""
    return build_names(table_rows(pages, group), group)
