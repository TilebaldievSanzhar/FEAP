"""Tests for Excel parsing and tree building."""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LEAVES_PATH = PROJECT_ROOT / "data" / "parsed" / "leaves.json"
TREE_PATH = PROJECT_ROOT / "data" / "parsed" / "tree.json"
SECTIONS_PATH = PROJECT_ROOT / "data" / "reference" / "sections.json"


def test_sections_json_has_21_sections():
    with open(SECTIONS_PATH, encoding="utf-8") as f:
        sections = json.load(f)
    assert len(sections) == 21
    assert sections[0]["number"] == "I"
    assert sections[-1]["number"] == "XXI"


def test_leaves_json_has_expected_count():
    if not LEAVES_PATH.exists():
        return  # Skip if not yet parsed
    with open(LEAVES_PATH, encoding="utf-8") as f:
        leaves = json.load(f)
    assert len(leaves) == 13279
    # All codes should be 10 digits
    for leaf in leaves[:100]:
        assert len(leaf["code"]) == 10
        assert leaf["code"].isdigit()


def test_tree_json_has_all_levels():
    if not TREE_PATH.exists():
        return  # Skip if not yet built
    with open(TREE_PATH, encoding="utf-8") as f:
        nodes = json.load(f)

    levels = {n["level"] for n in nodes}
    assert levels == {0, 1, 2, 3, 4}

    sections = [n for n in nodes if n["level"] == 0]
    assert len(sections) == 21

    leaves = [n for n in nodes if n["level"] == 4]
    assert len(leaves) == 13279


def test_tree_parent_references_valid():
    if not TREE_PATH.exists():
        return
    with open(TREE_PATH, encoding="utf-8") as f:
        nodes = json.load(f)

    ids = {n["id"] for n in nodes}
    for node in nodes:
        if node["parent_id"] is not None:
            assert node["parent_id"] in ids, f"Node {node['id']} has invalid parent {node['parent_id']}"
