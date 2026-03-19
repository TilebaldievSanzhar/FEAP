"""Parse section and group notes (примечания) from ETT nomenclature PDFs.

Source: data/ett_pdfs/ru.{01..97}.pdf (96 files, excluding group 77)
Output: data/parsed/notes.json

ETT PDFs have the following structure on their first pages:
  [Optional] РАЗДЕЛ {roman} + title + section notes
  ГРУППА {nn} + title + [group notes]
  [Optional] Дополнительные примечания / Примечания к субпозициям
  Code table (Код ТН ВЭД | Наименование ...)

This script extracts:
  - Section-level notes (scope_type="section", scope_code=roman numeral)
  - Group-level notes (scope_type="group", scope_code=2-digit code)
  - Additional notes (Дополнительные примечания) for groups
  - Subposition notes (Примечания к субпозициям) for groups
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import fitz  # PyMuPDF

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ETT_DIR = PROJECT_ROOT / "data" / "ett_pdfs"
OUTPUT_PATH = PROJECT_ROOT / "data" / "parsed" / "notes.json"

ALL_GROUPS = [f"{i:02d}" for i in range(1, 98) if i != 77]

# Roman numeral pattern
ROMAN = r"X{0,3}(?:IX|IV|V?I{0,3})"

# Markers in the PDF text
RE_SECTION_HEADER = re.compile(rf"^РАЗДЕЛ\s+({ROMAN})\s*$", re.MULTILINE)
RE_GROUP_HEADER = re.compile(r"^ГРУППА\s+(\d{1,2})\s*$", re.MULTILINE)
RE_NOTES_HEADER = re.compile(
    r"^(Примечани[яе]|Дополнительн(?:ое|ые)\s+примечани[яе]|"
    r"Примечани[яе]\s+к\s+субпозици[яиюейям]+)\s*:?\s*$",
    re.MULTILINE | re.IGNORECASE,
)
# Code table header — signals end of notes region
RE_TABLE_START = re.compile(r"^Код\s*$", re.MULTILINE)
RE_CODE_LINE = re.compile(r"^\s*\d{4}\s+\d{2}\s", re.MULTILINE)

# Note number at line start: "1.", "1 ", "2.", "3\n", etc.
# In ETT PDFs, notes are numbered like "1.\n" or "1. " at the beginning of a line
# Limit to 1-2 digits to avoid matching 4-digit position codes like "5504."
RE_NOTE_NUM = re.compile(r"^\s*(\d{1,2})\s*\.\s*$", re.MULTILINE)
# Alternative: note number followed by text on same line: "1. В данную..."
RE_NOTE_NUM_INLINE = re.compile(r"^\s*(\d{1,2})\s*\.\s+(\S.+)", re.MULTILINE)


def _extract_full_text(pdf_path: Path, max_pages: int = 30) -> str:
    """Extract text from first N pages of a PDF (notes are always at the start)."""
    doc = fitz.open(str(pdf_path))
    pages = min(max_pages, len(doc))
    text = "\n".join(doc[i].get_text() for i in range(pages))
    doc.close()
    return text


def _find_notes_region(text: str) -> tuple[str | None, str | None, str]:
    """Find section roman, group code, and the portion of text containing all notes.

    Returns (section_roman | None, group_code, notes_region_text).
    """
    section_roman = None
    group_code = None

    section_m = RE_SECTION_HEADER.search(text)
    if section_m:
        section_roman = section_m.group(1)

    group_m = RE_GROUP_HEADER.search(text)
    if group_m:
        group_code = group_m.group(1).zfill(2)

    # Notes region: from start of text to the code table
    # The code table starts with "Код\nТН ВЭД" or a 10-digit code line
    table_m = RE_TABLE_START.search(text)
    code_m = RE_CODE_LINE.search(text)

    end_pos = len(text)
    if table_m:
        end_pos = min(end_pos, table_m.start())
    if code_m:
        end_pos = min(end_pos, code_m.start())

    return section_roman, group_code, text[:end_pos]


def _classify_note(note_text: str) -> str:
    """Classify a note as exclusive, inclusive, definition, or general."""
    lower = note_text.lower()
    # Check exclusive FIRST (contains "включа" too)
    if "не включа" in lower or "не относ" in lower or "не распростран" in lower:
        return "exclusive"
    if "включа" in lower or "относ" in lower:
        return "inclusive"
    if ("означа" in lower or "определени" in lower or "понимается" in lower
            or "термин" in lower):
        return "definition"
    return "general"


def _extract_references(note_text: str) -> list[str] | None:
    """Extract references to other groups/sections/positions."""
    refs = set()
    # "группы 39", "группу 84", "группе 90"
    for m in re.finditer(r"групп[уеыаой]\s+(\d{2})", note_text):
        refs.add(m.group(1))
    # "товарной позиции 8207", "товарных позиций 7102"
    for m in re.finditer(r"позици[яиюейй]+\s+(\d{4})", note_text):
        refs.add(m.group(1))
    # "раздел XV", "раздела XVII"
    for m in re.finditer(rf"раздел[аеу]?\s+({ROMAN})\b", note_text, re.IGNORECASE):
        refs.add(m.group(1))
    refs.discard("")
    return sorted(refs) if refs else None


def _split_into_numbered_notes(notes_text: str) -> list[tuple[str, str]]:
    """Split notes text into (number, text) pairs.

    Handles two formats:
      Format 1: "1.\n<text>\n2.\n<text>"  (number on its own line)
      Format 2: "1. <text>\n2. <text>"    (number + text on same line)
    """
    # Try format 1 first: standalone numbers
    standalone_matches = list(RE_NOTE_NUM.finditer(notes_text))
    # Try format 2: inline numbers
    inline_matches = list(RE_NOTE_NUM_INLINE.finditer(notes_text))

    # Use whichever found more matches, preferring standalone
    if standalone_matches and len(standalone_matches) >= len(inline_matches):
        matches = standalone_matches
        results = []
        for i, m in enumerate(matches):
            num = m.group(1)
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(notes_text)
            text = notes_text[start:end].strip()
            if text:
                results.append((num, text))
        return results

    if inline_matches:
        results = []
        for i, m in enumerate(inline_matches):
            num = m.group(1)
            start = m.start()
            end = inline_matches[i + 1].start() if i + 1 < len(inline_matches) else len(notes_text)
            text = notes_text[start:end].strip()
            if text:
                # Remove the "N. " prefix from the text
                text = re.sub(r"^\d+\.\s*", "", text, count=1)
                results.append((num, text))
        return results

    # No numbered notes found — return entire text as a single note
    stripped = notes_text.strip()
    if stripped:
        return [("", stripped)]
    return []


def _clean_note_text(text: str) -> str:
    """Clean up extracted note text: normalize whitespace, remove page artifacts."""
    # Remove page breaks / headers that leak in
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    # Collapse single newlines within paragraphs (but keep double newlines)
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned.append("")
        else:
            cleaned.append(stripped)
    return "\n".join(cleaned).strip()


def _extract_notes_between(
    text: str,
    start_pos: int,
    end_pos: int,
    scope_type: str,
    scope_code: str,
    note_kind: str = "regular",
) -> list[dict]:
    """Extract individual notes from a text region between start_pos and end_pos."""
    region = text[start_pos:end_pos].strip()
    if not region:
        return []

    notes = []
    numbered = _split_into_numbered_notes(region)

    for num, note_text in numbered:
        note_text = _clean_note_text(note_text)
        if not note_text or len(note_text) < 5:
            continue

        note_number = num if num else None
        if note_kind == "additional" and note_number:
            note_number = f"доп.{note_number}"
        elif note_kind == "subposition" and note_number:
            note_number = f"субп.{note_number}"

        notes.append({
            "scope_type": scope_type,
            "scope_code": scope_code,
            "note_number": note_number,
            "note_type": _classify_note(note_text),
            "note_text": note_text,
            "references_to": _extract_references(note_text),
        })

    return notes


def parse_ett_pdf(pdf_path: Path, group_code: str) -> list[dict]:
    """Parse a single ETT PDF for section and group notes."""
    text = _extract_full_text(pdf_path)
    section_roman, parsed_group, notes_region = _find_notes_region(text)

    all_notes: list[dict] = []

    # Find all note headers in the notes region
    note_headers = list(RE_NOTES_HEADER.finditer(notes_region))
    group_m = RE_GROUP_HEADER.search(notes_region)

    # Determine section notes boundaries
    # Section notes: between first "Примечания:" and "ГРУППА XX"
    if section_roman and note_headers and group_m:
        first_notes = note_headers[0]
        # Only if notes header comes before group header (= section notes)
        if first_notes.start() < group_m.start():
            section_notes = _extract_notes_between(
                notes_region,
                first_notes.end(),
                group_m.start(),
                scope_type="section",
                scope_code=section_roman,
            )
            all_notes.extend(section_notes)

    # Find group notes: notes headers that come AFTER the group header
    if group_m:
        group_start = group_m.end()
        # Find note headers after the group header
        group_note_headers = [
            h for h in note_headers if h.start() > group_m.start()
        ]

        for i, header in enumerate(group_note_headers):
            # Determine end of this notes section
            if i + 1 < len(group_note_headers):
                end_pos = group_note_headers[i + 1].start()
            else:
                end_pos = len(notes_region)

            header_text = header.group(0).strip().lower()

            if "дополнительн" in header_text:
                kind = "additional"
            elif "субпозици" in header_text:
                kind = "subposition"
            else:
                kind = "regular"

            group_notes = _extract_notes_between(
                notes_region,
                header.end(),
                end_pos,
                scope_type="group",
                scope_code=group_code,
                note_kind=kind,
            )
            all_notes.extend(group_notes)

    return all_notes


def parse_all_notes() -> list[dict]:
    """Parse all ETT PDFs and extract section + group notes."""
    all_notes: list[dict] = []

    for group_code in ALL_GROUPS:
        pdf_path = ETT_DIR / f"ru.{group_code}.pdf"
        if not pdf_path.exists():
            print(f"  SKIP ru.{group_code}.pdf — not found")
            continue

        try:
            notes = parse_ett_pdf(pdf_path, group_code)
            if notes:
                section_count = sum(1 for n in notes if n["scope_type"] == "section")
                group_count = sum(1 for n in notes if n["scope_type"] == "group")
                print(f"  ru.{group_code}.pdf: {section_count} section + {group_count} group notes")
                all_notes.extend(notes)
            else:
                print(f"  ru.{group_code}.pdf: no notes found")
        except Exception as e:
            print(f"  ERROR ru.{group_code}.pdf: {e}")

    # Summary
    section_notes = [n for n in all_notes if n["scope_type"] == "section"]
    group_notes = [n for n in all_notes if n["scope_type"] == "group"]
    print(f"\nTotal: {len(all_notes)} notes "
          f"({len(section_notes)} section, {len(group_notes)} group)")

    # Check coverage
    sections_covered = {n["scope_code"] for n in section_notes}
    groups_with_notes = {n["scope_code"] for n in group_notes}
    print(f"Sections covered: {len(sections_covered)}/21")
    print(f"Groups with notes: {len(groups_with_notes)}/{len(ALL_GROUPS)}")

    return all_notes


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"Parsing notes from ETT PDFs in {ETT_DIR}...")
    notes = parse_all_notes()
    if notes:
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(notes, f, ensure_ascii=False, indent=2)
        print(f"\nWritten {len(notes)} notes to {OUTPUT_PATH}")
    else:
        print("No notes extracted. Ensure ETT PDFs are downloaded first.")
        print("Run: python scripts/download_ett_pdfs.py")


if __name__ == "__main__":
    main()
