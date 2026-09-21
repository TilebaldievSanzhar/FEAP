from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts.parse.ett_nomenclature import _count_dashes, parse_group_pdf
from scripts.seed.reference_patch import apply_patch


def test_numbered_and_virtual_parents_survive_leaf_and_sibling_boundaries(monkeypatch):
    import scripts.parse.ett_nomenclature as module
    lines = [
        '4407', 'Timber:', '– coniferous:', '4407 11', '– – pine:',
        '– – – other:', '– – – – planed:', '4407 11 340', '– – – – – species A:',
        '4407 11 340 1 – – – – – – moisture <= 22%',
        '– – – – other:', '4407 11 940', '– – – – – species A:',
        '4407 11 940 1', '– – – – – – moisture <= 22%',
        '4407 11 940 9 – – – – – – other',
        '4408', 'Veneer:', '4408 10', '– coniferous:', '4408 10 000 1 – – other',
    ]
    monkeypatch.setattr(module.fitz, 'open', lambda path: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(module, 'extract_group_name', lambda doc: 'Wood')
    monkeypatch.setattr(module, '_extract_table_lines', lambda doc: lines)
    r = parse_group_pdf(Path('fixture.pdf'), '44')
    assert _count_dashes('– – value') == 2
    assert r['leaves']['4407113401']['parent_names'] == [
        'coniferous', 'pine', 'other', 'planed', 'species A']
    assert r['leaves']['4407119401']['parent_names'] == [
        'coniferous', 'pine', 'other', 'other', 'species A']
    assert r['leaves']['4407119409']['parent_names'] == r['leaves']['4407119401']['parent_names']
    assert r['leaves']['4408100001']['parent_names'] == ['coniferous']
    assert r['subpositions']['440711']['full_name'] == 'coniferous > pine'


def connection(rows):
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = rows
    return conn, cursor


def test_reference_patch_checks_every_precondition_before_any_update():
    patch = {'changes': [{'code': '4407113401', 'before': 'old A', 'after': 'new A'},
                         {'code': '4407119401', 'before': 'old B', 'after': 'new B'}]}
    conn, cursor = connection([('4407113401', 'old A'), ('4407119401', 'different')])
    with pytest.raises(ValueError, match='Stale'):
        apply_patch(conn, patch, apply=True)
    cursor.executemany.assert_not_called()
    conn, cursor = connection([('4407113401', 'old A'), ('4407119401', 'old B')])
    assert apply_patch(conn, patch) == 2
    cursor.executemany.assert_not_called()
    assert apply_patch(conn, patch, apply=True) == 2
    assert cursor.executemany.call_args.args[1] == [('new A', '4407113401'), ('new B', '4407119401')]
    conn, cursor = connection([('4407113401', 'new A'), ('4407119401', 'new B')])
    assert apply_patch(conn, patch, apply=True, reverse=True) == 2
    assert cursor.executemany.call_args.args[1] == [('old A', '4407113401'), ('old B', '4407119401')]
