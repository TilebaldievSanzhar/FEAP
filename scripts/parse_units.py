"""Parse additional units of measurement from the official ЕТТ ЕАЭС table.

Source: sudact.ru — Решение Совета ЕЭК № 80 (ЕТТ ЕАЭС).
Each of the 97 groups has an HTML page with a 4-column table:
  Код ТН ВЭД | Наименование | Доп. ед. изм. | Ставка пошлины

This script fetches all 97 pages (one per group), extracts code→unit pairs,
and saves them to data/parsed/units.json.

Usage:
    python scripts/parse_units.py
"""

from __future__ import annotations

import json
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SECTIONS_PATH = PROJECT_ROOT / "data" / "reference" / "sections.json"
OUTPUT_PATH = PROJECT_ROOT / "data" / "parsed" / "units.json"

BASE_URL = (
    "https://sudact.ru/law/reshenie-soveta-evraziiskoi-ekonomicheskoi-komissii-ot-14092021_7"
    "/edinaia-tovarnaia-nomenklatura-vneshneekonomicheskoi-deiatelnosti"
)

# Groups that don't exist in ТН ВЭД (reserved)
SKIP_GROUPS = {77}

ROMAN_TO_SLUG = {
    "I": "i", "II": "ii", "III": "iii", "IV": "iv", "V": "v",
    "VI": "vi", "VII": "vii", "VIII": "viii", "IX": "ix", "X": "x",
    "XI": "xi", "XII": "xii", "XIII": "xiii", "XIV": "xiv", "XV": "xv",
    "XVI": "xvi", "XVII": "xvii", "XVIII": "xviii", "XIX": "xix",
    "XX": "xx", "XXI": "xxi",
}

REQUEST_DELAY = 1.5  # seconds between requests


class DataTableParser(HTMLParser):
    """Extract rows from the ЕТТ data table (identified by border="1px" attr).

    The page has multiple nested tables (layout + data). The data table
    has `border="1px"` and contains 4 columns:
      0: Код ТН ВЭД
      1: Наименование
      2: Доп. ед. изм.
      3: Ставка пошлины

    Cell content is wrapped in <p> and <a> tags, or <pre></pre> for empty cells.
    """

    def __init__(self):
        super().__init__()
        self.in_data_table = False
        self.table_depth = 0
        self.data_table_depth = 0
        self.in_row = False
        self.in_cell = False
        self.current_row: list[str] = []
        self.current_cell: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = dict(attrs)

        if tag == "table":
            self.table_depth += 1
            if attr_dict.get("border") == "1px":
                self.in_data_table = True
                self.data_table_depth = self.table_depth
        elif tag == "tr" and self.in_data_table:
            self.in_row = True
            self.current_row = []
        elif tag in ("td", "th") and self.in_row:
            self.in_cell = True
            self.current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self.in_cell:
            self.in_cell = False
            self.current_row.append("".join(self.current_cell).strip())
        elif tag == "tr" and self.in_row:
            self.in_row = False
            if self.current_row:
                self.rows.append(self.current_row)
        elif tag == "table":
            if self.table_depth == self.data_table_depth:
                self.in_data_table = False
            self.table_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.current_cell.append(data)


def load_sections() -> list[dict]:
    with open(SECTIONS_PATH, encoding="utf-8") as f:
        return json.load(f)


def build_group_urls(sections: list[dict]) -> dict[int, str]:
    """Build mapping: group_number → full URL."""
    urls: dict[int, str] = {}
    for sec in sections:
        slug = ROMAN_TO_SLUG[sec["number"]]
        for g in range(sec["groups_from"], sec["groups_to"] + 1):
            if g in SKIP_GROUPS:
                continue
            urls[g] = f"{BASE_URL}/razdel-{slug}/gruppa-{g:02d}/"
    return urls


def normalize_code(raw: str) -> str | None:
    """Convert '8501 10 100 1' → '8501101001'. Returns None if not a valid code."""
    digits = re.sub(r"\s+", "", raw)
    if not digits.isdigit():
        return None
    return digits


UNIT_NORMALIZATION = {
    "л100% спирта": "л 100% спирта",
    "кгP2O5": "кг P2O5",
    "кгK2O": "кг K2O",
    "м2": "м²",
    "м3": "м³",
    "1000 м3": "1000 м³",
}


def normalize_unit(raw: str) -> str | None:
    """Normalize unit value: '-' → None, otherwise strip and unify formatting."""
    cleaned = raw.strip()
    if cleaned in ("-", "–", "—", ""):
        return None
    return UNIT_NORMALIZATION.get(cleaned, cleaned)


def parse_html(html: str) -> dict[str, str | None]:
    """Parse HTML page, extract code→unit mapping from the data table.

    Table rows may have 4 or 5 columns (some pages have an extra empty
    first column from <pre></pre>). We detect the code column by finding
    which column contains the 10-digit code pattern.
    """
    parser = DataTableParser()
    parser.feed(html)

    result: dict[str, str | None] = {}

    for row in parser.rows:
        if len(row) < 3:
            continue

        # Find the column that contains the code (may be col 0 or col 1)
        code = None
        code_col = -1
        for ci, cell in enumerate(row):
            candidate = normalize_code(cell)
            if candidate and len(candidate) == 10:
                code = candidate
                code_col = ci
                break

        if code is None:
            continue

        # Unit column is 2 positions after the code column
        unit_col = code_col + 2
        if unit_col < len(row):
            unit = normalize_unit(row[unit_col])
        else:
            unit = None

        result[code] = unit

    return result


def fetch_all_units() -> dict[str, str | None]:
    """Fetch and parse all 97 group pages."""
    sections = load_sections()
    group_urls = build_group_urls(sections)

    all_units: dict[str, str | None] = {}
    total = len(group_urls)

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        for i, (group_num, url) in enumerate(sorted(group_urls.items()), 1):
            print(f"  [{i:2d}/{total}] Group {group_num:02d}...", end=" ", flush=True)

            try:
                resp = client.get(url)
                resp.raise_for_status()
                units = parse_html(resp.text)
                all_units.update(units)
                unit_count = sum(1 for v in units.values() if v is not None)
                print(f"{len(units)} codes ({unit_count} with units)")
            except httpx.HTTPError as e:
                print(f"ERROR: {e}", file=sys.stderr)

            if i < total:
                time.sleep(REQUEST_DELAY)

    return all_units


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    print("Fetching units of measurement from ЕТТ ЕАЭС (sudact.ru)...")
    units = fetch_all_units()

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(units, f, ensure_ascii=False, indent=2)

    total = len(units)
    with_units = sum(1 for v in units.values() if v is not None)
    print(f"\nDone! {total} codes total, {with_units} with additional units.")
    print(f"Written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
