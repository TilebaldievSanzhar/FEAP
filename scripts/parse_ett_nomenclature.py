"""Parse ETT nomenclature PDFs into a single nomenclature.json.

Reads: data/ett_pdfs/ru.{01..97}.pdf (96 files, excluding group 77)
Produces: data/parsed/nomenclature.json

The PDF contains the official ТН ВЭД nomenclature with:
- Position names (4-digit codes)
- Subposition names (6-digit codes) with virtual parent nodes
- Leaf names (8/10-digit codes)
- Virtual intermediate nodes (dash-hierarchy, no own code)

Virtual nodes provide context like "миндаль > в скорлупе" instead of
just "в скорлупе" — solving the 17% naming duplication problem.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import fitz  # PyMuPDF

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ETT_DIR = PROJECT_ROOT / "data" / "ett_pdfs"
OUTPUT_PATH = PROJECT_ROOT / "data" / "parsed" / "nomenclature.json"

ALL_GROUPS = [f"{i:02d}" for i in range(1, 98) if i != 77]

# --- Regexes ---
# Some PDFs use '+' or '*' prefix for footnote markers — stripped before matching
RE_LINE_PREFIX = re.compile(r"^[+*]\s*")
RE_CODE_4 = re.compile(r"^\s*(\d{4})\s*$")
RE_CODE_6 = re.compile(r"^\s*(\d{4})\s+(\d{2})\s*$")
# 8-digit: XXXX XX XXX or XXXX XX XX
RE_CODE_8a = re.compile(r"^\s*(\d{4})\s+(\d{2})\s+(\d{3})\s*$")
RE_CODE_8b = re.compile(r"^\s*(\d{4})\s+(\d{2})\s+(\d{2})\s*$")
RE_CODE_10 = re.compile(r"^\s*(\d{4})\s+(\d{2})\s+(\d{3})\s+(\d)\s*(.*)")
RE_DASHES = re.compile(r"^((?:[–\u2013]\s*)+)")

HEADER_WORDS = frozenset([
    "Код", "ТН ВЭД", "Наименование позиции", "Доп.",
    "ед.", "изм.", "Ставка ввозной", "таможенной",
    "пошлины", "(в процентах", "от таможенной",
    "стоимости либо", "в евро, либо", "в долларах США)",
])


def _count_dashes(text: str) -> int:
    """Count leading en-dashes: '– – – name' → 3."""
    m = RE_DASHES.match(text.strip())
    if not m:
        return 0
    return m.group(1).count("–") + m.group(1).count("\u2013")


def _strip_dashes(text: str) -> str:
    """'– – name:' → 'name'."""
    return re.sub(r"^(?:[–\u2013]\s*)+", "", text).strip().rstrip(":")


def _is_header(line: str) -> bool:
    s = line.strip()
    return s in HEADER_WORDS


def _is_junk(line: str) -> bool:
    """Rate values, units, and other non-name lines."""
    s = line.strip()
    if not s:
        return True
    if _is_header(line):
        return True
    # Standalone dash (rate = "no data")
    if s in ("–", "\u2013", "-"):
        return True
    # Pure numbers / rate codes: "5", "15", "0", "397С)", "563С)"
    if re.match(r"^\d+[,.]?\d*\s*(%|С\)|)\s*$", s):
        return True
    # Units
    if s in ("шт", "кг", "л", "м", "м2", "м3", "пар", "г", "1000 шт",
             "м²", "м³", "тыс. шт"):
        return True
    # Complex rate: "5, но не менее 0,02 евро за 1 кг63С)"
    # But NOT measurement names like "714,29 дтекс, но не менее"
    if re.match(r"^\d+[,.]", s) and ("евро" in s or "долл" in s):
        return True
    if re.match(r"^\d+[,.]", s) and "менее" in s and ("евро" in s or "долл" in s or "кг" in s):
        return True
    if "евро за" in s or "долларов за" in s:
        return True
    return False


def _looks_like_text(line: str) -> bool:
    """Could this line be continuation of a name?"""
    s = line.strip()
    if not s:
        return False
    if _is_junk(line):
        return False
    if re.match(r"^\d{4}\s", s):
        return False
    return True


def extract_group_name(doc: fitz.Document) -> str:
    """Extract group name from the first page."""
    text = doc[0].get_text()
    lines = text.split("\n")

    collecting = False
    parts = []
    for line in lines:
        s = line.strip()
        if re.match(r"^ГРУППА\s+\d+", s):
            collecting = True
            continue
        if collecting:
            if not s:
                if parts:
                    break
                continue
            if s.startswith("Примечани") or s.startswith("Дополнител"):
                break
            if s == s.upper() and s[0].isalpha():
                parts.append(s)
            elif not parts:
                parts.append(s)
            else:
                break

    return " ".join(parts).rstrip(":")


def _extract_table_lines(doc: fitz.Document) -> list[str]:
    """Get all content lines from table pages, skipping headers."""
    lines: list[str] = []
    for page in doc:
        text = page.get_text()
        page_lines = text.split("\n")

        if not any("Наименование позиции" in l for l in page_lines):
            continue

        past_header = False
        for line in page_lines:
            if not past_header:
                if "в долларах США)" in line:
                    past_header = True
                continue
            lines.append(line)

    return lines


def parse_group_pdf(pdf_path: Path, group_code: str) -> dict:
    """Parse a single group PDF into structured nomenclature data."""
    doc = fitz.open(str(pdf_path))
    group_name = extract_group_name(doc)
    raw_lines = _extract_table_lines(doc)
    doc.close()

    # Helper: clean a raw line (strip +/* footnote prefixes)
    def clean(raw: str) -> str:
        return RE_LINE_PREFIX.sub(" ", raw)

    # Helper: check if a code belongs to this group
    def _code_in_group(code_4: str) -> bool:
        return code_4[:2] == group_code

    # Helper: check if a cleaned line matches any code regex for THIS group
    def _match_any_code(ln: str) -> bool:
        cl = clean(ln)
        for rx in (RE_CODE_10, RE_CODE_8a, RE_CODE_8b, RE_CODE_6, RE_CODE_4):
            m = rx.match(cl)
            if m and _code_in_group(m.group(1)):
                return True
        return False

    # Helper: collect the name for a code_6/code_8 entry (1-3 lines after code)
    def _collect_subpos_name(start: int) -> tuple[list[str], int]:
        parts = []
        j = start
        while j < len(raw_lines):
            cl = clean(raw_lines[j])
            if _is_junk(cl):
                j += 1
                continue
            if _match_any_code(raw_lines[j]):
                break
            parts.append(cl.strip())
            j += 1
            # After getting first name line, check if next meaningful line is code/virtual
            if parts:
                k = j
                while k < len(raw_lines) and _is_junk(clean(raw_lines[k])):
                    k += 1
                if k < len(raw_lines):
                    nxt = clean(raw_lines[k]).strip()
                    if _match_any_code(raw_lines[k]):
                        break
                    if nxt.startswith("–") and len(nxt) > 2:
                        break
        return parts, j

    # First pass: classify lines into entries
    entries: list[dict] = []
    i = 0

    while i < len(raw_lines):
        line = clean(raw_lines[i])
        stripped = line.strip()

        if not stripped or _is_header(line):
            i += 1
            continue

        # 10-digit leaf
        m = RE_CODE_10.match(line)
        if m and _code_in_group(m.group(1)):
            code = m.group(1) + m.group(2) + m.group(3) + m.group(4)
            name_on_line = m.group(5).strip()
            dashes = _count_dashes(name_on_line) if name_on_line else 0
            name_parts = [name_on_line] if name_on_line else []

            j = i + 1
            while j < len(raw_lines) and _looks_like_text(clean(raw_lines[j])):
                name_parts.append(clean(raw_lines[j]).strip())
                j += 1

            entries.append({
                "type": "leaf",
                "code": code,
                "dash_level": dashes,
                "name": " ".join(name_parts).strip(),
            })
            i = j
            continue

        # 8-digit: XXXX XX XXX
        m = RE_CODE_8a.match(line)
        if m and _code_in_group(m.group(1)):
            code_6 = m.group(1) + m.group(2)
            name_parts, j = _collect_subpos_name(i + 1)
            full_name = " ".join(name_parts).strip()
            entries.append({
                "type": "subposition",
                "code": code_6,
                "dash_level": _count_dashes(full_name),
                "name": full_name,
            })
            i = j
            continue

        # 8-digit: XXXX XX XX
        m = RE_CODE_8b.match(line)
        if m and _code_in_group(m.group(1)):
            code_6 = m.group(1) + m.group(2)
            name_parts, j = _collect_subpos_name(i + 1)
            full_name = " ".join(name_parts).strip()
            entries.append({
                "type": "subposition",
                "code": code_6,
                "dash_level": _count_dashes(full_name),
                "name": full_name,
            })
            i = j
            continue

        # 6-digit subposition
        m = RE_CODE_6.match(line)
        if m and _code_in_group(m.group(1)):
            code = m.group(1) + m.group(2)
            name_parts, j = _collect_subpos_name(i + 1)
            full_name = " ".join(name_parts).strip()
            entries.append({
                "type": "subposition",
                "code": code,
                "dash_level": _count_dashes(full_name),
                "name": full_name,
            })
            i = j
            continue

        # 4-digit position
        m = RE_CODE_4.match(line)
        if m and _code_in_group(m.group(1)):
            code = m.group(1)
            name_parts = []
            j = i + 1
            while j < len(raw_lines):
                cl = clean(raw_lines[j])
                if not _looks_like_text(cl):
                    break
                s = cl.strip()
                if s.startswith("–") and len(s) > 2:
                    break
                name_parts.append(s)
                j += 1

            entries.append({
                "type": "position",
                "code": code,
                "dash_level": 0,
                "name": " ".join(name_parts).strip().rstrip(":"),
            })
            i = j
            continue

        # Virtual node
        if stripped.startswith("–") or stripped.startswith("\u2013"):
            dashes = _count_dashes(stripped)
            text_after = _strip_dashes(stripped)
            if dashes > 0 and len(text_after) > 1:
                name_parts = [stripped]
                j = i + 1
                while j < len(raw_lines):
                    cl = clean(raw_lines[j])
                    if not _looks_like_text(cl):
                        break
                    nxt = cl.strip()
                    if nxt.startswith("–") and _count_dashes(nxt) > 0:
                        break
                    name_parts.append(nxt)
                    j += 1

                entries.append({
                    "type": "virtual",
                    "code": None,
                    "dash_level": dashes,
                    "name": " ".join(name_parts).strip(),
                })
                i = j
                continue

        i += 1

    # Second pass: build nomenclature from entries using virtual parent stack
    positions: dict[str, dict] = {}
    subpositions: dict[str, dict] = {}
    leaves: dict[str, dict] = {}
    virtual_stack: list[tuple[int, str]] = []  # (dash_level, name)

    for entry in entries:
        etype = entry["type"]
        dash = entry["dash_level"]
        name_raw = entry["name"]

        if etype == "position":
            virtual_stack = []
            positions[entry["code"]] = {
                "name": _strip_dashes(name_raw),
                "group": group_code,
            }

        elif etype == "virtual":
            # Pop entries at same or deeper level
            while virtual_stack and virtual_stack[-1][0] >= dash:
                virtual_stack.pop()
            virtual_stack.append((dash, _strip_dashes(name_raw)))

        elif etype == "subposition":
            # Pop virtual parents at levels >= this entry's dash level
            while virtual_stack and virtual_stack[-1][0] >= dash:
                virtual_stack.pop()
            own_name = _strip_dashes(name_raw)
            vparents = [vp[1] for vp in virtual_stack]
            full_name = " > ".join(vparents + [own_name]) if vparents else own_name

            code = entry["code"]
            # Only set if not already present (code_6 takes priority over code_8)
            if code not in subpositions:
                subpositions[code] = {
                    "position": code[:4],
                    "own_name": own_name,
                    "virtual_parents": list(vparents),
                    "full_name": full_name,
                }

        elif etype == "leaf":
            # Pop virtual parents at levels >= this leaf's dash level
            while virtual_stack and virtual_stack[-1][0] >= dash:
                virtual_stack.pop()
            own_name = _strip_dashes(name_raw)
            vparents = [vp[1] for vp in virtual_stack]

            code = entry["code"]
            subpos_code = code[:6]
            leaves[code] = {
                "subposition": subpos_code,
                "name": own_name,
            }

            # Create implicit subposition if needed
            if subpos_code not in subpositions:
                sp_full = " > ".join(vparents + [own_name]) if vparents else own_name
                subpositions[subpos_code] = {
                    "position": subpos_code[:4],
                    "own_name": own_name,
                    "virtual_parents": list(vparents),
                    "full_name": sp_full,
                }

    # Derive missing positions from subpositions
    # When PDF has "0205 00" (code_6) instead of separate "0205" (code_4),
    # the subposition name IS the position name.
    all_pos_codes = set(positions.keys())
    for sp_code, sp_data in subpositions.items():
        pos_code = sp_code[:4]
        if pos_code not in all_pos_codes:
            # Use the first subposition's own_name as the position name
            positions[pos_code] = {
                "name": sp_data["own_name"],
                "group": group_code,
            }
            all_pos_codes.add(pos_code)

    return {
        "group_name": group_name,
        "positions": positions,
        "subpositions": subpositions,
        "leaves": leaves,
    }


def parse_all() -> dict:
    """Parse all 96 group PDFs into unified nomenclature."""
    result = {
        "meta": {
            "source": "eec.eaeunion.org",
            "parsed_at": datetime.now(timezone.utc).isoformat(),
            "files_count": 0,
        },
        "groups": {},
        "positions": {},
        "subpositions": {},
        "leaves": {},
    }

    for group_code in ALL_GROUPS:
        pdf_path = ETT_DIR / f"ru.{group_code}.pdf"
        if not pdf_path.exists():
            print(f"  MISSING: {pdf_path.name}")
            continue

        data = parse_group_pdf(pdf_path, group_code)

        result["groups"][group_code] = {"name": data["group_name"]}
        result["positions"].update(data["positions"])
        result["subpositions"].update(data["subpositions"])
        result["leaves"].update(data["leaves"])
        result["meta"]["files_count"] += 1

        n_pos = len(data["positions"])
        n_sub = len(data["subpositions"])
        n_leaf = len(data["leaves"])
        n_virt = sum(1 for sp in data["subpositions"].values() if sp["virtual_parents"])
        print(
            f"  {group_code}: {n_pos} pos, {n_sub} subpos "
            f"({n_virt} with virtual parents), {n_leaf} leaves"
        )

    return result


def print_stats(nom: dict) -> None:
    print("\nNomenclature statistics:")
    print(f"  Groups:       {len(nom['groups'])}")
    print(f"  Positions:    {len(nom['positions'])}")
    print(f"  Subpositions: {len(nom['subpositions'])}")
    print(f"  Leaves:       {len(nom['leaves'])}")

    from collections import Counter
    by_position: dict[str, list[str]] = {}
    for code, sp in nom["subpositions"].items():
        pos = sp["position"]
        by_position.setdefault(pos, []).append(sp["full_name"])

    total_multi = 0
    dup_count = 0
    for pos, names in by_position.items():
        if len(names) < 2:
            continue
        total_multi += 1
        if len(names) != len(set(names)):
            dup_count += 1

    if total_multi:
        unique_pct = (1 - dup_count / total_multi) * 100
        print(f"  Positions with unique subposition names: "
              f"{total_multi - dup_count}/{total_multi} ({unique_pct:.1f}%)")
        if dup_count:
            print(f"  Positions with duplicate subposition names: {dup_count}")


def main() -> None:
    print("Parsing ETT nomenclature PDFs...")
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    nom = parse_all()
    print_stats(nom)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(nom, f, ensure_ascii=False, indent=2)
    print(f"\nWritten to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
