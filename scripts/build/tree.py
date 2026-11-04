"""Build hierarchical tree from nomenclature.json + leaves.json.

Reads:
  data/parsed/nomenclature.json — names for all hierarchy levels (from EEC PDFs)
  data/parsed/leaves.json       — leaf codes + duty rates (from TWS Excel)
  data/reference/sections.json  — section definitions

Produces:
  data/parsed/tree.json — full tree ready for DB seeding

Hierarchy levels:
  0 = Section (I-XXI)
  1 = Group (2 digits: 01-97)
  2 = Position (4 digits)
  3 = Subposition (6 digits)
  4 = Leaf (10 digits)

Leaf ``name_full`` is the EEC path below the heading («с содержанием жира…: 🠺
более 3 мас.%: 🠺 прочие»). TWS composes its own path and in places attaches
an unnumbered row to the wrong leaf (0401 20 990 0 → «не более 3 мас.%»), so
TWS text is only a cross-check of the leaf's own name and a fallback. The build
refuses a tree whose siblings share a name or whose references lost a number.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LEAVES_PATH = PROJECT_ROOT / "data" / "parsed" / "leaves.json"
SECTIONS_PATH = PROJECT_ROOT / "data" / "reference" / "sections.json"
NOMENCLATURE_PATH = PROJECT_ROOT / "data" / "parsed" / "nomenclature.json"
OUTPUT_PATH = PROJECT_ROOT / "data" / "parsed" / "tree.json"


def load_sections() -> list[dict]:
    with open(SECTIONS_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_leaves() -> list[dict]:
    with open(LEAVES_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_nomenclature() -> dict:
    if not NOMENCLATURE_PATH.exists():
        raise FileNotFoundError(
            f"{NOMENCLATURE_PATH} not found — run parse_ett_nomenclature.py first"
        )
    with open(NOMENCLATURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def group_to_section(group_num: int, sections: list[dict]) -> dict | None:
    for s in sections:
        if s["groups_from"] <= group_num <= s["groups_to"]:
            return s
    return None


def clean_date_suffix(name: str) -> str:
    return re.sub(r"\s*\(с \d{2}\.\d{2}\.\d{4}\)\s*", " ", name).strip()


#: Separator of hierarchy levels in a leaf name_full (as in TWS and the L5 prompt).
LEAF_SEP = ": 🠺 "
#: Latin look-alikes in TWS text («hiррoglossus» with Cyrillic р) must not decide.
_HOMOGLYPHS = str.maketrans("аеорсухкмтнвАЕОРСУХКМТНВ", "aeopcyxkmthbAEOPCYXKMTHB")
#: A heading reference that lost its number: «…для оборудования товарных позиций».
_REFERENCE = re.compile(
    r"(?<!данной )(?<!части )(?<!предыдущих )(?<!вышеприведенных )(?<!более )"
    r"\b(?:товарн\w*\s+позици\w*|(?:под)?субпозици\w*)"
    r"(?=\s*(?:$|[,;:.)>]|\s(?:или|и|кроме|либо|в|с|из|для)\b))",
    re.IGNORECASE,
)


def name_key(text: str) -> str:
    """Comparison key: letters and digits only, case and look-alikes folded."""
    text = clean_date_suffix(text or "").replace("³", "3").replace("²", "2").replace("º", "°")
    return re.sub(r"[^0-9a-zа-я%°]+", "", text.replace("ё", "е").lower().translate(_HOMOGLYPHS))


def leaf_names(leaf: dict, eec: dict | None) -> tuple[str, str, bool]:
    """(name, name_full, from_eec) of a TWS leaf.

    The EEC path is used when its own text is the leaf's own TWS text up to
    typos (or a fuller wording of it); otherwise TWS stays and is reported.
    """
    if eec:
        tws_own, eec_own = name_key(leaf["name_leaf"]), name_key(eec["name"])
        same = SequenceMatcher(None, tws_own, eec_own).ratio() >= 0.85
        fuller = len(tws_own) >= 15 and tws_own in eec_own
        if tws_own and (same or fuller):
            path = [clean_date_suffix(p) for p in (*eec["parent_names"], eec["name"])]
            return path[-1], LEAF_SEP.join(path), True
    return clean_date_suffix(leaf["name_leaf"]), clean_date_suffix(leaf["name_full"]), False


def lint_names(nodes: list[dict]) -> list[str]:
    """Defects that make the tree unusable for classification."""
    problems: list[str] = []
    siblings: dict[int, dict[str, str]] = defaultdict(dict)
    for n in nodes:
        if n["parent_id"] is None:
            continue
        key = name_key(n["name_full"] or n["name"])
        twin = siblings[n["parent_id"]].setdefault(key, n["code"])
        if twin != n["code"]:
            problems.append(f"{twin} и {n['code']}: одинаковое полное наименование у соседей")
    for n in nodes:
        text = n["name_full"] or n["name"]
        m = _REFERENCE.search(text) if n["level"] >= 2 else None
        if m:
            problems.append(f"{n['code']}: ссылка без номера «…{text[max(0, m.start() - 40):m.end()]}»")
    return problems


def build_tree() -> list[dict]:
    sections = load_sections()
    leaves = load_leaves()
    nom = load_nomenclature()
    nom_groups = nom.get("groups", {})
    nom_positions = nom.get("positions", {})
    nom_subpositions = nom.get("subpositions", {})
    nom_leaves = nom.get("leaves", {})
    tws_named: list[str] = []

    # Group leaves by hierarchy codes
    by_group: dict[str, list[dict]] = defaultdict(list)
    by_position: dict[str, list[dict]] = defaultdict(list)
    by_subposition: dict[str, list[dict]] = defaultdict(list)

    for leaf in leaves:
        by_group[leaf["group_code"]].append(leaf)
        by_position[leaf["position_code"]].append(leaf)
        by_subposition[leaf["subposition_code"]].append(leaf)

    nodes: list[dict] = []
    node_id = 0

    section_ids: dict[str, int] = {}
    group_ids: dict[str, int] = {}
    position_ids: dict[str, int] = {}
    subpos_ids: dict[str, int] = {}

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

        # Name from nomenclature (EEC PDF)
        name = nom_groups.get(group_code, {}).get("name", "")
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

        # Name from nomenclature
        name = nom_positions.get(pos_code, {}).get("name", "")
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

        # Name from nomenclature — full_name includes virtual parents
        sp_data = nom_subpositions.get(subpos_code, {})
        name = sp_data.get("full_name", "")
        if not name:
            name = sp_data.get("own_name", "")
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
            continue

        parent_node = next((n for n in nodes if n["id"] == subpos_ids[subpos_code]), None)
        sec_roman = parent_node["section_roman"] if parent_node else None
        group_code = parent_node["group_code"] if parent_node else leaf["group_code"]
        name, name_full, from_eec = leaf_names(leaf, nom_leaves.get(leaf["code"]))
        if not from_eec:
            tws_named.append(leaf["code"])

        node_id += 1
        nodes.append({
            "id": node_id,
            "code": leaf["code"],
            "code_formatted": leaf["code_formatted"],
            "level": 4,
            "parent_id": subpos_ids[subpos_code],
            "name": name,
            "name_full": name_full,
            "section_roman": sec_roman,
            "group_code": group_code,
            "duty_rate": leaf["duty_rate"],
            "is_leaf": True,
        })

    if tws_named:
        # Codes of another ETT edition or a mismatching own name: kept as in TWS.
        print(f"Leaves named from TWS, not EEC ({len(tws_named)}): {', '.join(tws_named)}")
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
    problems = lint_names(nodes)
    if problems:
        print(f"\nTree NOT written: {len(problems)} naming defects", *problems[:50], sep="\n  ")
        sys.exit(1)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(nodes, f, ensure_ascii=False, indent=2)
    print(f"Written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
