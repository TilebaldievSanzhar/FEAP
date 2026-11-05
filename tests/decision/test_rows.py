"""Строки ЕТТ под позицией: путь кода, соседи по порядку документа, строка кода любой длины.

Фикстура ``rows_sample.json`` — листья позиций из ``tnved_tree`` после исправления
имён (b27eb84): код и полный путь ЕЭК через «: 🠺 ».
"""
import json
from pathlib import Path

import pytest

from feap.decision.rows import SEP, PositionRows

SAMPLE = json.loads((Path(__file__).resolve().parents[1] / 'fixtures' / 'decision' / 'rows_sample.json')
                    .read_text(encoding='utf-8'))


def tree(position: str) -> PositionRows:
    data = SAMPLE['positions'][position]
    return PositionRows(position, data['name'], [tuple(leaf) for leaf in data['leaves']])


def texts(rows):
    return [r.text for r in rows]


def test_separator_is_the_one_the_tree_builder_writes():
    source = (Path(__file__).resolve().parents[2] / 'scripts' / 'build' / 'tree.py').read_text(encoding='utf-8')
    assert f'LEAF_SEP = "{SEP}"' in source


def test_leaf_path_keeps_unnumbered_rows():
    t = tree('0207')
    assert texts(t.path(t.leaf('0207146009').id)) == [
        'Мясо и пищевые субпродукты домашней птицы, указанной в товарной позиции 0105, свежие, охлажденные '
        'или замороженные',
        'кур домашних (Gallus domesticus)', 'части тушек и субпродукты, замороженные', 'части тушек',
        'необваленные', 'ножки и куски из них', 'прочие',
    ]


def test_siblings_follow_the_order_of_the_document():
    t = tree('0207')
    legs = t.node('02071460')
    assert legs.text == 'ножки и куски из них'
    assert texts(t.siblings(legs.id)) == [
        'половины или четвертины', 'крылья целые, с тонким концом или без него',
        'спинки, шейки, спинки вместе с шейкой, гузки и тонкие концы крыльев', 'грудки и куски из них',
        'ножки и куски из них', 'прочие',
    ]
    assert t.siblings('0207') == []


def test_code_of_any_length_maps_to_its_row():
    t = tree('0207')
    assert t.node('020714').text == 'части тушек и субпродукты, замороженные'
    assert t.node('0207') is t.root
    assert t.node('0208') is None
    other = t.node('02071470')
    assert other.text == 'прочие' and t.exact(other, '02071470')
    assert t.rows[other.parent].text == 'необваленные'


def test_same_text_under_different_parents_is_two_rows():
    t = tree('0401')
    low, high = t.leaf('0401201900'), t.leaf('0401209900')
    assert low.text == high.text == 'прочие' and low.id != high.id
    assert t.rows[low.parent].text == 'не более 3 мас.%' and t.rows[high.parent].text == 'более 3 мас.%'


def test_ids_number_rows_by_order():
    t = tree('0207')
    assert t.leaf('0207146009').id.startswith('0207/') and t.root.id == '0207'
    assert t.rows[t.leaf('0207146009').id].parent == t.node('02071460').id


def test_twin_leaves_are_refused():
    with pytest.raises(ValueError, match='тот же путь'):
        PositionRows('0101', 'Лошади', [('0101210000', f'лошади{SEP}прочие'), ('0101290000', f'лошади{SEP}прочие')])


def test_leaf_inside_another_path_is_refused():
    with pytest.raises(ValueError, match='начало пути'):
        PositionRows('0101', 'Лошади', [('0101210000', 'лошади'), ('0101290000', f'лошади{SEP}прочие')])
