"""Строки ЕТТ под товарной позицией: путь кода, соседи, порядок.

Дерево ``tnved_tree`` хранит узлы только для кодов. Безномерные строки
(«– – – необваленные:») есть в полном наименовании листа: путь ЕЭК под
позицией через «: 🠺 » (scripts/build/tree.py, v5). Из путей всех листьев
позиции, взятых в порядке кодов, восстанавливается дерево строк: соседи стоят в
порядке документа, а это нужно для «прочие» — оно исключает *предшествующих*
соседей.

Идентификатор строки — номер по порядку на каждом уровне: «0207/1.2.1.2.6».
Он зависит только от справочника, поэтому одинаков в прогоне и в отчёте.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

#: Разделитель строк в ``name_full`` листа (U+1F83A), как ``LEAF_SEP`` в scripts/build/tree.py.
SEP = ': \U0001F83A '


@dataclass
class Row:
    id: str
    text: str
    parent: str | None  # None — сама товарная позиция
    index: int          # номер среди соседей, с нуля
    children: list[str] = field(default_factory=list)
    leaves: list[str] = field(default_factory=list)  # листья под строкой, по порядку кодов

    @property
    def is_leaf(self) -> bool:
        return not self.children


class PositionRows:
    """Товарная позиция и её строки; корень — сама позиция с идентификатором-кодом."""

    def __init__(self, position: str, name: str, leaves: Iterable[tuple[str, str | None]]):
        self.position = position
        self.rows: dict[str, Row] = {position: Row(position, name, None, 0)}
        self._leaf: dict[str, str] = {}
        for code, full in sorted(leaves):
            if not code.startswith(position):
                raise ValueError(f'{code} не относится к позиции {position}')
            node = self.rows[position]
            node.leaves.append(code)
            for text in (full or name).split(SEP):
                child = next((c for c in node.children if self.rows[c].text == text), None)
                if child is None:
                    prefix = f'{node.id}.' if node.parent else f'{position}/'
                    child = f'{prefix}{len(node.children) + 1}'
                    self.rows[child] = Row(child, text, node.id, len(node.children))
                    node.children.append(child)
                node = self.rows[child]
                node.leaves.append(code)
            if node.id in self._leaf.values():
                raise ValueError(f'{code}: тот же путь, что у другого листа ({node.id})')
            self._leaf[code] = node.id
        for code, row_id in self._leaf.items():
            if self.rows[row_id].children:
                raise ValueError(f'{code}: путь листа — начало пути другого листа ({row_id})')

    @property
    def root(self) -> Row:
        return self.rows[self.position]

    def leaf(self, code: str) -> Row:
        return self.rows[self._leaf[code]]

    def node(self, code: str) -> Row | None:
        """Строка кода любой длины: общий предок его листьев («0207 14 60» → «ножки и куски из них»)."""
        if code in self._leaf:
            return self.leaf(code)
        wanted = [leaf for leaf in self.root.leaves if leaf.startswith(code)]
        if not wanted:
            return None
        for row in reversed(self.path(self._leaf[wanted[0]])):
            if set(wanted) <= set(row.leaves):
                return row
        return self.root

    def exact(self, row: Row, code: str) -> bool:
        """Под строкой ровно листья кода, без чужих."""
        return all(leaf.startswith(code) for leaf in row.leaves)

    def path(self, row_id: str) -> list[Row]:
        """Строки от позиции до ``row_id`` включительно."""
        chain = []
        node: Row | None = self.rows[row_id]
        while node is not None:
            chain.append(node)
            node = self.rows[node.parent] if node.parent else None
        return chain[::-1]

    def siblings(self, row_id: str) -> list[Row]:
        """Дети родителя строки по порядку, включая её саму; у позиции — пусто."""
        parent = self.rows[row_id].parent
        return [self.rows[c] for c in self.rows[parent].children] if parent else []

    def context(self, row_id: str) -> list[str]:
        """Тексты позиции и строк выше ``row_id``, сверху вниз."""
        return [row.text for row in self.path(row_id)[:-1]]


def load_headings(cursor, groups: Iterable[str]) -> dict[str, list[tuple[str, str]]]:
    """Товарные позиции групп по порядку кодов: для «прочие» в тексте позиции («Мебель прочая»)."""
    out: dict[str, list[tuple[str, str]]] = {}
    for group in sorted(set(groups)):
        cursor.execute(
            'SELECT code, name FROM tnved_tree WHERE length(code) = 4 AND code LIKE %s ORDER BY code',
            (group + '%',),
        )
        out[group] = [(code, name) for code, name in cursor.fetchall()]
    return out


def load_positions(cursor, positions: Iterable[str]) -> dict[str, PositionRows]:
    """Строки позиций из ``tnved_tree`` (DB-API курсор, paramstyle ``%s``)."""
    out: dict[str, PositionRows] = {}
    for position in sorted(set(positions)):
        cursor.execute('SELECT name FROM tnved_tree WHERE code = %s', (position,))
        found = cursor.fetchone()
        if found is None:
            continue
        cursor.execute(
            'SELECT code, name_full FROM tnved_tree WHERE is_leaf AND code LIKE %s ORDER BY code',
            (position + '%',),
        )
        out[position] = PositionRows(position, found[0], cursor.fetchall())
    return out
