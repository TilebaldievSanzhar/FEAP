"""Build hierarchical tree from flat 10-digit leaf codes.

Reads data/parsed/leaves.json + data/reference/sections.json.
Produces data/parsed/tree.json — full tree ready for DB seeding.

Hierarchy levels:
  0 = Section (I-XXI) — from sections.json
  1 = Group (2 digits: 01-97)
  2 = Position (4 digits)
  3 = Subposition (6 digits)
  4 = Subsubposition (10 digits = leaf)

Note: 8-digit level is skipped when it equals 6-digit + "00" (no branching).
Only creates 8-digit intermediate nodes when there are multiple 10-digit children
under the same 8-digit prefix AND the 8-digit prefix differs from 6-digit+"00XX".
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LEAVES_PATH = PROJECT_ROOT / "data" / "parsed" / "leaves.json"
SECTIONS_PATH = PROJECT_ROOT / "data" / "reference" / "sections.json"
OUTPUT_PATH = PROJECT_ROOT / "data" / "parsed" / "tree.json"


def load_sections() -> list[dict]:
    with open(SECTIONS_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_leaves() -> list[dict]:
    with open(LEAVES_PATH, encoding="utf-8") as f:
        return json.load(f)


def group_to_section(group_num: int, sections: list[dict]) -> dict | None:
    for s in sections:
        if s["groups_from"] <= group_num <= s["groups_to"]:
            return s
    return None


def infer_parent_name(leaves_in_scope: list[dict], part_index: int) -> str:
    """Infer name of a parent node from the name_parts of its children.

    The name_parts in the Excel follow a hierarchy where earlier parts
    describe higher-level groupings. We take the most common first part
    among children.
    """
    names: dict[str, int] = defaultdict(int)
    for leaf in leaves_in_scope:
        parts = leaf["name_parts"]
        if len(parts) > part_index:
            name = parts[part_index]
            # Clean trailing colons and whitespace
            name = name.rstrip(":").strip()
            names[name] += 1
    if not names:
        return ""
    # Return the most common name
    return max(names, key=lambda k: names[k])


def clean_date_suffix(name: str) -> str:
    return re.sub(r"\s*\(с \d{2}\.\d{2}\.\d{4}\)\s*", " ", name).strip()


def build_tree() -> list[dict]:
    sections = load_sections()
    leaves = load_leaves()

    # Group leaves by their hierarchy codes
    by_group: dict[str, list[dict]] = defaultdict(list)
    by_position: dict[str, list[dict]] = defaultdict(list)
    by_subposition: dict[str, list[dict]] = defaultdict(list)

    for leaf in leaves:
        by_group[leaf["group_code"]].append(leaf)
        by_position[leaf["position_code"]].append(leaf)
        by_subposition[leaf["subposition_code"]].append(leaf)

    nodes: list[dict] = []
    node_id = 0

    # ID tracking for parent references
    section_ids: dict[str, int] = {}  # roman -> id
    group_ids: dict[str, int] = {}    # "01" -> id
    position_ids: dict[str, int] = {} # "0101" -> id
    subpos_ids: dict[str, int] = {}   # "010121" -> id

    # Level 0: Sections
    for sec in sections:
        node_id += 1
        section_ids[sec["number"]] = node_id
        nodes.append({
            "id": node_id,
            "code": sec["number"],
            "code_formatted": sec["number"],
            "level": 0,
            "parent_id": None,
            "name": sec["name"],
            "name_full": sec["name"],
            "section_roman": sec["number"],
            "group_code": None,
            "duty_rate": None,
            "is_leaf": False,
        })

    # Level 1: Groups (2-digit)
    for group_code in sorted(by_group.keys()):
        group_num = int(group_code)
        sec = group_to_section(group_num, sections)
        if sec is None:
            print(f"WARNING: No section for group {group_code}")
            continue

        group_leaves = by_group[group_code]
        # Infer group name from the first name_part of its leaves
        # The first part often contains the group-level description
        name = infer_parent_name(group_leaves, 0)
        if not name:
            name = f"Группа {group_code}"

        node_id += 1
        group_ids[group_code] = node_id
        nodes.append({
            "id": node_id,
            "code": group_code,
            "code_formatted": group_code,
            "level": 1,
            "parent_id": section_ids[sec["number"]],
            "name": clean_date_suffix(name),
            "name_full": clean_date_suffix(name),
            "section_roman": sec["number"],
            "group_code": group_code,
            "duty_rate": None,
            "is_leaf": False,
        })

    # Level 2: Positions (4-digit)
    for pos_code in sorted(by_position.keys()):
        group_code = pos_code[:2]
        if group_code not in group_ids:
            continue

        pos_leaves = by_position[pos_code]
        # Position name is typically at index 0 of name_parts
        # but for 4-digit positions, it could be the full first segment
        name = infer_parent_name(pos_leaves, 0)
        if not name:
            name = f"Позиция {pos_code}"

        sec_roman = None
        for n in nodes:
            if n["code"] == group_code and n["level"] == 1:
                sec_roman = n["section_roman"]
                break

        node_id += 1
        position_ids[pos_code] = node_id
        nodes.append({
            "id": node_id,
            "code": pos_code,
            "code_formatted": f"{pos_code[:2]}.{pos_code[2:]}",
            "level": 2,
            "parent_id": group_ids[group_code],
            "name": clean_date_suffix(name),
            "name_full": clean_date_suffix(name),
            "section_roman": sec_roman,
            "group_code": group_code,
            "duty_rate": None,
            "is_leaf": False,
        })

    # Level 3: Subpositions (6-digit)
    for subpos_code in sorted(by_subposition.keys()):
        pos_code = subpos_code[:4]
        if pos_code not in position_ids:
            continue

        subpos_leaves = by_subposition[subpos_code]
        # Subposition name: try to get a meaningful name
        # Often at name_parts index 1 (after position name)
        name = infer_parent_name(subpos_leaves, 1)
        if not name:
            name = infer_parent_name(subpos_leaves, 0)
        if not name:
            name = f"Субпозиция {subpos_code}"

        parent_node = next((n for n in nodes if n["id"] == position_ids[pos_code]), None)
        sec_roman = parent_node["section_roman"] if parent_node else None
        group_code = parent_node["group_code"] if parent_node else subpos_code[:2]

        node_id += 1
        subpos_ids[subpos_code] = node_id
        nodes.append({
            "id": node_id,
            "code": subpos_code,
            "code_formatted": f"{subpos_code[:4]} {subpos_code[4:6]}",
            "level": 3,
            "parent_id": position_ids[pos_code],
            "name": clean_date_suffix(name),
            "name_full": clean_date_suffix(name),
            "section_roman": sec_roman,
            "group_code": group_code,
            "duty_rate": None,
            "is_leaf": False,
        })

    # Level 4: Leaf nodes (10-digit)
    for leaf in leaves:
        subpos_code = leaf["subposition_code"]
        if subpos_code not in subpos_ids:
            # Try to find or create parent
            continue

        parent_node = next((n for n in nodes if n["id"] == subpos_ids[subpos_code]), None)
        sec_roman = parent_node["section_roman"] if parent_node else None
        group_code = parent_node["group_code"] if parent_node else leaf["group_code"]

        node_id += 1
        nodes.append({
            "id": node_id,
            "code": leaf["code"],
            "code_formatted": leaf["code_formatted"],
            "level": 4,
            "parent_id": subpos_ids[subpos_code],
            "name": clean_date_suffix(leaf["name_leaf"]),
            "name_full": clean_date_suffix(leaf["name_full"]),
            "section_roman": sec_roman,
            "group_code": group_code,
            "duty_rate": leaf["duty_rate"],
            "is_leaf": True,
        })

    return nodes


def print_stats(nodes: list[dict]) -> None:
    from collections import Counter
    levels = Counter(n["level"] for n in nodes)
    print("Tree statistics:")
    level_names = {0: "Sections", 1: "Groups", 2: "Positions", 3: "Subpositions", 4: "Leaves"}
    for lvl in sorted(levels):
        print(f"  Level {lvl} ({level_names.get(lvl, '?')}): {levels[lvl]}")
    print(f"  Total: {len(nodes)}")


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    nodes = build_tree()
    print_stats(nodes)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(nodes, f, ensure_ascii=False, indent=2)
    print(f"Written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
