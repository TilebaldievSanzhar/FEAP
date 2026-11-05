"""Какие условия проверять для кода каскада (план, §7, Этап 1).

Условия — сырые тексты строк ЕТТ, без компиляции:

- путь выбранного кода: текст позиции и строки до листа;
- соседи на каждой развилке пути; у широкой развилки — до ``cap`` ближайших по порядку;
- пути альтернатив из трассы каскада (кандидаты L3, L4, L5);
- для каждого «прочие» — предшествующие конкретные соседи: без них остаток не вычислить.

Тип строки определяется по тексту:

- сноска 5) или «в порядке, указанном в дополнительном примечании» — режим: из
  описания не выводится, по умолчанию не заявлен (презумпция);
- «прочие» / «прочий» / «прочая» / «прочее» — остаток; с уточнением
  («креветки прочие», «прочие, с содержанием жира») — остаток ∧ уточнение.
  «… и прочие пищевые субпродукты» — перечисление, а не остаток;
- вся строка — сравнение с числом («массой не более 80 кг») — числовое условие;
- остальное — семантическое условие для исполнителя.

Текст товарной позиции с «прочие» («Мебель прочая и ее части», 9403) — остаток среди
позиций своей группы: он исключает предшествующие позиции. Для этого нужны позиции
группы (``headings``); без них текст позиции проверяется как обычное условие.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from feap.decision.contracts import Condition, ConditionKind
from feap.decision.logic import parse_interval
from feap.decision.rows import PositionRows, Row

#: Сноска «5)» в ЕТТ — подтверждение режима документом (гражданские воздушные суда,
#: промышленная сборка). Другие сноски не меняют смысла строки.
REGIME_FOOTNOTE = '5'
_FOOTNOTE = re.compile(r'\s(\d{1,2})\)$')
_QUOTA = re.compile(r'в порядке, указанном в дополнительном примечании', re.IGNORECASE)
_RESIDUAL = re.compile(r'(?<![А-Яа-яЁё])(прочие|прочий|прочая|прочее)(?![А-Яа-яЁё])', re.IGNORECASE)
_ENUMERATION = re.compile(r'(?:^|\s)(?:и|или)\s*$')
_INTERPRETIVE = re.compile(r'товарн\w*\s+позици|субпозици|примечани|групп[аыеи]\w*\s+\d', re.IGNORECASE)


def split_footnote(text: str) -> tuple[str, str | None]:
    """«для гражданских воздушных судов 5)» → (текст, «5»); «(1: 1)» сноской не считается."""
    m = _FOOTNOTE.search(text)
    if not m:
        return text, None
    head = text[:m.start()]
    if head.count('(') != head.count(')'):
        return text, None  # скобка закрывает открытую раньше: «… (1: 1)»
    return head.rstrip(), m.group(1)


def split_residual(text: str) -> tuple[bool, str]:
    """(это «прочие», уточнение без слова «прочие»)."""
    for m in _RESIDUAL.finditer(text):
        if _ENUMERATION.search(text[:m.start()]):
            continue  # «верблюды и прочие животные …» — перечисление
        rest = re.sub(r'\s+', ' ', text[:m.start()] + text[m.end():])
        rest = re.sub(r'\s+([,;:)])', r'\1', rest).strip(' ,;')
        return True, rest
    return False, text


def classify(text: str, *, heading: bool = False) -> dict:
    """Поля ``Condition`` по тексту строки (без id, позиции и контекста)."""
    if heading:
        # «Мебель прочая и ее части» — остаток среди позиций группы; «… и прочие проводники» — нет
        is_residual, rest = split_residual(text)
        question = rest if is_residual and rest else text
        return {'kind': ConditionKind.SEMANTIC, 'residual': is_residual, 'question': question,
                'interpretive': bool(_INTERPRETIVE.search(question)),
                'has_number': bool(re.search(r'\d', question))}
    body, footnote = split_footnote(text)
    fields: dict = {'footnote': footnote}
    if footnote == REGIME_FOOTNOTE or _QUOTA.search(body):
        return {**fields, 'kind': ConditionKind.REGIME}
    is_residual, rest = split_residual(body)
    fields['residual'] = is_residual
    if is_residual and not rest:
        return {**fields, 'kind': ConditionKind.RESIDUAL}
    parsed = parse_interval(rest)
    if parsed:
        quantity, interval = parsed
        return {**fields, 'kind': ConditionKind.NUMERIC, 'quantity': quantity, 'interval': interval}
    return {**fields, 'kind': ConditionKind.SEMANTIC, 'question': rest,
            'interpretive': bool(_INTERPRETIVE.search(rest)), 'has_number': bool(re.search(r'\d', rest))}


@dataclass
class Fork:
    """Развилка на пути кода: строка пути и проверяемые соседи (включая её)."""

    row: str
    considered: list[str]
    omitted: int


@dataclass
class CaseConditions:
    conditions: dict[str, Condition] = field(default_factory=dict)
    chosen: list[str] = field(default_factory=list)  # путь кода каскада: позиция → лист
    forks: list[Fork] = field(default_factory=list)
    alternatives: dict[str, list[str]] = field(default_factory=dict)  # код → путь
    preceding: dict[str, list[str]] = field(default_factory=dict)  # «прочие» → проверяемые соседи до него
    omitted_preceding: dict[str, int] = field(default_factory=dict)  # «прочие» → сколько соседей не проверено
    notes: list[str] = field(default_factory=list)


def nearest(siblings: list[Row], row: Row, cap: int) -> list[Row]:
    """До ``cap`` соседей, ближайших к строке по порядку; строка всегда среди них."""
    ranked = sorted(siblings, key=lambda s: (abs(s.index - row.index), s.index))[:max(cap, 1)]
    return sorted(ranked, key=lambda s: s.index)


class _Builder:
    def __init__(self, trees: dict[str, PositionRows], cap: int,
                 headings: dict[str, list[tuple[str, str]]] | None = None):
        self.trees = trees
        self.cap = cap
        self.headings = headings or {}
        self.out = CaseConditions()

    def add(self, tree: PositionRows, row: Row) -> Condition:
        if row.parent is None:
            return self.add_heading(tree.position, row.text)
        if row.id not in self.out.conditions:
            self.out.conditions[row.id] = Condition(
                id=row.id, position=tree.position, text=row.text, context=tree.context(row.id),
                **classify(row.text),
            )
        return self.out.conditions[row.id]

    def add_heading(self, code: str, name: str) -> Condition:
        if code not in self.out.conditions:
            fields = classify(name, heading=True)
            if fields['residual'] and code[:2] not in self.headings:
                fields.update(residual=False, question=name)  # без позиций группы остаток не вычислить
            self.out.conditions[code] = Condition(id=code, position=code, text=name, **fields)
        return self.out.conditions[code]

    def heading_scope(self, code: str) -> None:
        """Позиции группы до «… прочие» — в проверку, как соседи до строки «прочие»."""
        condition = self.out.conditions[code]
        if code in self.out.preceding or not condition.residual:
            return
        before = [(c, n) for c, n in self.headings.get(code[:2], []) if c < code]
        checked = before[-self.cap:] if self.cap else []
        for other, name in checked:
            self.add_heading(other, name)
        self.out.preceding[code] = [c for c, _ in checked]
        self.out.omitted_preceding[code] = len(before) - len(checked)
        for other, _ in checked:
            self.heading_scope(other)

    def pure_residual(self, tree: PositionRows, row: Row) -> bool:
        return self.add(tree, row).kind is ConditionKind.RESIDUAL

    def residual_scope(self, tree: PositionRows, row: Row, considered: list[Row] | None) -> None:
        """Соседей до «прочие» — в проверку: без их статусов остаток не вычислить."""
        if row.id in self.out.preceding or not self.add(tree, row).residual:
            return
        before = [s for s in tree.siblings(row.id) if s.index < row.index and not self.pure_residual(tree, s)]
        if considered is None:
            considered = before[-self.cap:] if self.cap else []
        checked = [s for s in before if s in considered]
        for sibling in checked:
            self.add(tree, sibling)
        self.out.preceding[row.id] = [s.id for s in checked]
        self.out.omitted_preceding[row.id] = len(before) - len(checked)
        for sibling in checked:  # «двигатели … прочие» до «прочие» — тоже остаток
            self.residual_scope(tree, sibling, considered)


def build_conditions(
    trees: dict[str, PositionRows], chosen_code: str, alternatives: Iterable[str] = (), *, cap: int = 15,
    headings: dict[str, list[tuple[str, str]]] | None = None,
) -> CaseConditions:
    """Условия для кода каскада и альтернатив из его трассы; ``headings`` — позиции групп по порядку."""
    tree = trees.get(chosen_code[:4])
    if tree is None:
        raise ValueError(f'Нет строк позиции {chosen_code[:4]}')
    try:
        leaf = tree.leaf(chosen_code)
    except KeyError:
        raise ValueError(f'{chosen_code} — не лист позиции {tree.position}') from None
    b = _Builder(trees, cap, headings)
    path = tree.path(leaf.id)
    b.out.chosen = [row.id for row in path]
    for row in path:
        b.add(tree, row)
    b.heading_scope(tree.position)
    for row in path[1:]:
        siblings = tree.siblings(row.id)
        considered = nearest(siblings, row, cap)
        for sibling in considered:
            b.add(tree, sibling)
        b.out.forks.append(Fork(row.id, [s.id for s in considered], len(siblings) - len(considered)))
        b.residual_scope(tree, row, considered)
        for sibling in considered:
            b.residual_scope(tree, sibling, considered)

    for code in dict.fromkeys(re.sub(r'\D', '', c) for c in alternatives):
        alt_tree = trees.get(code[:4])
        node = alt_tree.node(code) if alt_tree and len(code) >= 4 else None
        if node is None:
            b.out.notes.append(f'альтернатива {code}: нет в справочнике')
            continue
        if alt_tree is tree and node.id in b.out.chosen:
            continue  # предок выбранного листа — это сам путь каскада
        if not alt_tree.exact(node, code):
            b.out.notes.append(f'альтернатива {code}: строка {node.id} шире кода')
        alt_path = alt_tree.path(node.id)
        for row in alt_path:
            b.add(alt_tree, row)
        b.heading_scope(alt_tree.position)
        for row in alt_path[1:]:
            b.residual_scope(alt_tree, row, None)
        b.out.alternatives[code] = [row.id for row in alt_path]
    return b.out
