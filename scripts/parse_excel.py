"""Parse TWS Excel file into structured JSON for DB seeding.

The Excel contains 13,279 rows of 10-digit ТН ВЭД leaf codes.
Each row: (code_10, name_with_arrows, duty_rate, link).
Name uses ' 🠺 ' as hierarchy level separator.

This script produces data/parsed/leaves.json with all leaf nodes.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import openpyxl

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXCEL_PATH = next(PROJECT_ROOT.glob("data/TWS_TNVED*.xlsx"))
OUTPUT_PATH = PROJECT_ROOT / "data" / "parsed" / "leaves.json"


def format_code(code: str) -> str:
    """Format 10-digit code as 'XXXX XX XXX X'."""
    c = code.ljust(10, "0")
    return f"{c[:4]} {c[4:6]} {c[6:9]} {c[9]}"


def extract_hierarchy_names(full_name: str) -> list[str]:
    """Split '🠺'-separated name into hierarchy levels."""
    parts = full_name.split(" 🠺 ")
    return [p.strip().rstrip(":") for p in parts]


def clean_name(name: str) -> str:
    """Remove date annotations like '(с 01.01.2022)' from names."""
    return re.sub(r"\s*\(с \d{2}\.\d{2}\.\d{4}\)\s*", " ", name).strip()


def parse_excel() -> list[dict]:
    wb = openpyxl.load_workbook(str(EXCEL_PATH), read_only=True)
    ws = wb["ТНВЭД"]

    leaves: list[dict] = []
    for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True)):
        code_raw, name_raw, duty_rate, _ = row
        if code_raw is None:
            continue

        code = str(code_raw).strip().ljust(10, "0")
        if not code.isdigit() or len(code) != 10:
            print(f"Skipping non-code row {i+2}: {code_raw}", file=sys.stderr)
            continue

        name_clean = clean_name(str(name_raw))
        name_parts = extract_hierarchy_names(name_clean)

        group_code = code[:2]
        position_code = code[:4]
        subposition_code = code[:6]
        code_8 = code[:8]

        leaves.append({
            "code": code,
            "code_formatted": format_code(code),
            "group_code": group_code,
            "position_code": position_code,
            "subposition_code": subposition_code,
            "code_8": code_8,
            "name_parts": name_parts,
            "name_full": str(name_raw).strip(),
            "name_leaf": name_parts[-1] if name_parts else str(name_raw).strip(),
            "duty_rate": str(duty_rate).strip() if duty_rate else None,
        })

    wb.close()
    print(f"Parsed {len(leaves)} leaf codes")
    return leaves


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    leaves = parse_excel()
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(leaves, f, ensure_ascii=False, indent=2)
    print(f"Written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
