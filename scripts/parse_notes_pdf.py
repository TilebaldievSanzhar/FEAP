"""Parse section/group notes from PDF files.

Notes are typically embedded within the same explanatory PDFs.
They appear at the beginning of each group PDF as "Примечания" sections.

This script extracts structured notes from the already-parsed text.
It can also work with a dedicated notes PDF if available.

Produces data/parsed/notes.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PSN_DIR = PROJECT_ROOT / "data" / "pdfs" / "psn"
OUTPUT_PATH = PROJECT_ROOT / "data" / "parsed" / "notes.json"

NOTE_HEADER = re.compile(
    r"(?:Примечани[яе]|ПРИМЕЧАНИ[ЯЕ])\s*(?:к\s+(?:разделу|группе|подгруппе))?\s*\.?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
NOTE_NUMBER = re.compile(r"^\s*(\d+[\.\)а-е]?)\s*[\.–\-]\s*(.+)", re.MULTILINE)


def extract_text_from_pdf(pdf_path: Path) -> str:
    import fitz

    doc = fitz.open(str(pdf_path))
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    return text


def extract_notes_from_text(text: str, group_code: str) -> list[dict]:
    """Extract structured notes from raw PDF text."""
    notes: list[dict] = []

    # Find "Примечания" sections
    matches = list(NOTE_HEADER.finditer(text))
    if not matches:
        return []

    for i, match in enumerate(matches):
        # Get text from this header to the next header or end of section
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else min(start + 5000, len(text))

        # Look for position header that would end notes section
        pos_match = re.search(r"^\d{2}\.\d{2}\b", text[start:end], re.MULTILINE)
        if pos_match:
            end = start + pos_match.start()

        notes_text = text[start:end].strip()
        if not notes_text:
            continue

        # Parse individual notes
        note_matches = list(NOTE_NUMBER.finditer(notes_text))
        if note_matches:
            for j, nm in enumerate(note_matches):
                note_end = note_matches[j + 1].start() if j + 1 < len(note_matches) else len(notes_text)
                note_text = notes_text[nm.start():note_end].strip()

                # Determine note type
                note_type = "general"
                lower = note_text.lower()
                if "не включа" in lower or "не относ" in lower:
                    note_type = "exclusive"
                elif "включа" in lower or "относ" in lower:
                    note_type = "inclusive"
                elif "означа" in lower or "определени" in lower or "понимается" in lower:
                    note_type = "definition"

                # Extract references to other groups/sections
                references = re.findall(
                    r"(?:групп[уеыа]\s+|позици[яиюей]\s+)(\d{2}(?:\.\d{2})?)",
                    note_text,
                )

                notes.append({
                    "scope_type": "group",
                    "scope_code": group_code,
                    "note_number": nm.group(1).rstrip(".").rstrip(")"),
                    "note_type": note_type,
                    "note_text": note_text,
                    "references_to": references if references else None,
                })
        else:
            # Single note block without numbering
            notes.append({
                "scope_type": "group",
                "scope_code": group_code,
                "note_number": None,
                "note_type": "general",
                "note_text": notes_text,
                "references_to": None,
            })

    return notes


def parse_all_notes() -> list[dict]:
    all_notes: list[dict] = []

    pdf_files = sorted(PSN_DIR.glob("psn*.pdf"))
    if not pdf_files:
        print(f"No PDF files found in {PSN_DIR}")
        print("Run scripts/download_pdfs.py first")
        return []

    print(f"Parsing notes from {len(pdf_files)} PDFs...")

    for pdf_path in pdf_files:
        group_code = pdf_path.stem.replace("psn", "")
        try:
            text = extract_text_from_pdf(pdf_path)
            notes = extract_notes_from_text(text, group_code)
            if notes:
                print(f"  {pdf_path.name}: {len(notes)} notes")
                all_notes.extend(notes)
        except Exception as e:
            print(f"  ERROR {pdf_path.name}: {e}")

    print(f"\nTotal notes extracted: {len(all_notes)}")
    return all_notes


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    notes = parse_all_notes()
    if notes:
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(notes, f, ensure_ascii=False, indent=2)
        print(f"Written to {OUTPUT_PATH}")
    else:
        print("No notes extracted. Ensure PDFs are downloaded first.")


if __name__ == "__main__":
    main()
