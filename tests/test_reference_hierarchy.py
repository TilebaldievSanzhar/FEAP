"""Names of the tnved tree: leaf paths from EEC, the build lint and reviewed DB patches.

The PDF reading itself is covered by tests/tariff/test_ett_names.py.
"""
from unittest.mock import MagicMock

import pytest

from scripts.build.tree import leaf_names, lint_names, name_key
from scripts.seed.reference_patch import apply_patch, diff_tree


def connection(rows):
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = rows
    return conn, cursor


# ── Leaf names: EEC path, TWS as a cross-check ──────────────────────────────

def test_leaf_name_comes_from_the_eec_path():
    tws = {"name_leaf": "прочие", "name_full": "Молоко…: 🠺 с содержанием жира…: 🠺 не более 3 мас.%: 🠺 прочие"}
    eec = {"name": "прочие", "parent_names": ["с содержанием жира…", "более 3 мас.%"]}
    assert leaf_names(tws, eec) == ("прочие", "с содержанием жира…: 🠺 более 3 мас.%: 🠺 прочие", True)


def test_leaf_keeps_tws_text_when_eec_row_is_another_leaf():
    tws = {"name_leaf": "асинхронные", "name_full": "мощностью до 7,5 кВт: 🠺 асинхронные"}
    eec = {"name": "для гражданских воздушных судов 5)", "parent_names": ["мощностью до 7,5 кВт"]}
    assert leaf_names(tws, eec) == ("асинхронные", "мощностью до 7,5 кВт: 🠺 асинхронные", False)
    assert leaf_names(tws, None)[2] is False  # код другой редакции ЕТТ


def test_leaf_accepts_typo_fixes_and_a_fuller_official_wording():
    tws = {"name_leaf": "палтус (Hiррoglossus hiррoglossus)", "name_full": "…"}
    eec = {"name": "палтус (Hippoglossus hippoglossus)", "parent_names": []}
    assert leaf_names(tws, eec)[2] is True
    tws = {"name_leaf": "с гидроксильным числом не более 100", "name_full": "…"}
    eec = {"name": "простые полиэфиры спиртов с гидроксильным числом не более 100", "parent_names": ["прочие"]}
    assert leaf_names(tws, eec)[1] == "прочие: 🠺 простые полиэфиры спиртов с гидроксильным числом не более 100"


def test_name_key_folds_formatting_but_not_meaning():
    assert name_key("кг/м³ , но") == name_key("кг/м3, но")
    assert name_key("другой - заднюю") == name_key("другой – заднюю")
    assert name_key("не более 3 мас.%") != name_key("более 3 мас.%")


def test_lint_finds_twin_siblings_and_references_that_lost_a_number():
    nodes = [
        {"id": 1, "code": "040120", "level": 3, "parent_id": None, "name": "x", "name_full": "x"},
        {"id": 2, "code": "0401201900", "level": 4, "parent_id": 1, "name": "прочие",
         "name_full": "не более 3 мас.%: 🠺 прочие"},
        {"id": 3, "code": "0401209900", "level": 4, "parent_id": 1, "name": "прочие",
         "name_full": "не более 3 мас.%: 🠺 прочие"},
        {"id": 4, "code": "8431", "level": 2, "parent_id": None, "name": "Части для оборудования товарных позиций",
         "name_full": "Части для оборудования товарных позиций"},
        {"id": 5, "code": "820590", "level": 3, "parent_id": None,
         "name": "прочие, включая наборы изделий из двух или более субпозиций данной товарной позиции",
         "name_full": "прочие, включая наборы изделий из двух или более субпозиций данной товарной позиции"},
    ]
    problems = lint_names(nodes)
    assert len(problems) == 2
    assert problems[0].startswith("0401201900 и 0401209900")
    assert problems[1].startswith("8431: ссылка без номера")


# ── Reviewed patches of the database ────────────────────────────────────────

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


def test_reference_patch_updates_both_names_of_a_heading():
    patch = {"changes": [
        {"code": "8431", "field": "name", "before": "Части", "after": "Части 8425 – 8430"},
        {"code": "8431", "field": "name_full", "before": "Части", "after": "Части 8425 – 8430"},
    ]}
    conn, cursor = connection([("8431", "Части")])
    assert apply_patch(conn, patch, apply=True) == 2
    assert [c.args for c in cursor.executemany.call_args_list] == [
        ("UPDATE tnved_tree SET name=%s WHERE code=%s", [("Части 8425 – 8430", "8431")]),
        ("UPDATE tnved_tree SET name_full=%s WHERE code=%s", [("Части 8425 – 8430", "8431")]),
    ]


@pytest.mark.parametrize("change, message", [
    ({"code": "8431", "field": "duty_rate", "before": "5", "after": "0"}, "Unsupported field"),
    ({"code": "XVI", "before": "a", "after": "b"}, "code"),
    ({"code": "84311", "before": "a", "after": "b"}, "code"),
])
def test_reference_patch_rejects_other_fields_and_codes(change, message):
    with pytest.raises(ValueError, match=message):
        apply_patch(MagicMock(), {"changes": [change]})


def test_diff_tree_lists_name_changes_and_refuses_another_structure():
    nodes = [
        {"id": 1, "code": "XVI", "level": 0, "parent_id": None, "name": "Раздел", "name_full": "Раздел"},
        {"id": 2, "code": "84", "level": 1, "parent_id": 1, "name": "Группа", "name_full": "Группа"},
        {"id": 3, "code": "8431", "level": 2, "parent_id": 2, "name": "Части 8425 – 8430",
         "name_full": "Части 8425 – 8430"},
    ]
    stored = [("XVI", 0, None, "Раздел", "Раздел"), ("84", 1, "XVI", "Группа", "Группа"),
              ("8431", 2, "84", "Части", "Части")]
    assert diff_tree(connection(stored)[0], nodes) == [
        {"code": "8431", "field": "name", "before": "Части", "after": "Части 8425 – 8430"},
        {"code": "8431", "field": "name_full", "before": "Части", "after": "Части 8425 – 8430"},
    ]
    moved = [*stored[:2], ("8431", 2, "XVI", "Части", "Части")]
    with pytest.raises(ValueError, match="structure"):
        diff_tree(connection(moved)[0], nodes)
    with pytest.raises(ValueError, match="Code sets"):
        diff_tree(connection(stored[:2])[0], nodes)
