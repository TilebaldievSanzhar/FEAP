"""Parse explanatory notes PDFs (пояснения) into structured sections.

Reads PDFs from data/pdfs/psn/psn{01-97}.pdf
Produces data/parsed/explanatory_notes.json

Each PDF covers one group (e.g., psn85.pdf = group 85).
Structure inside PDF:
  - General notes for the group
  - Per-position explanations (XX.YY pattern like 85.01)
  - Includes/excludes sections within each position

Output: one record per logical section (general / includes / excludes),
NOT per chunk. Full text is preserved for direct DB loading.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PSN_DIR = PROJECT_ROOT / "data" / "pdfs" / "psn"
OUTPUT_PATH = PROJECT_ROOT / "data" / "parsed" / "explanatory_notes.json"

# ── Regex patterns ──────────────────────────────────────────────────────────

POSITION_HEADER = re.compile(r"^(\d{2})\.(\d{2})\b")

SUBPOSITION_HEADER = re.compile(r"^(\d{4})\.(\d{2})\b")

# Excludes markers (checked BEFORE includes, because "не включаются" contains "включаются")
EXCLUDES_MARKERS = re.compile(
    r"(?:"
    r"[Вв] данную (?:позицию|товарную позицию|субпозицию|группу) (?:также )?не включа(?:ют|е)ся"
    r"|[Вв] данную (?:позицию|товарную позицию|субпозицию|группу) не входят"
    r"|[Нн]е включа(?:ют|е)ся"
    r"|[Сс]юда не включа(?:ют|е)ся"
    r")",
    re.IGNORECASE,
)

# Includes markers
INCLUDES_MARKERS = re.compile(
    r"(?:"
    r"[Вв] данную (?:позицию|товарную позицию|субпозицию|группу) (?:также )?включа(?:ют|е)ся"
    r"|[Сс]юда (?:также )?включа(?:ют|е)ся"
    r"|[Кк] данной категории относятся"
    r"|[Вв]ключа(?:ют|е)ся также"
    r")",
    re.IGNORECASE,
)

# Extract referenced codes from excludes: "(товарная позиция 85.02)" or "(группа 84)"
REFERENCE_PATTERN = re.compile(
    r"\((?:товарн(?:ая|ые) позици(?:я|и)|группа|позици(?:я|и))\s+"
    r"(\d{2}\.\d{2})\)",
    re.IGNORECASE,
)


def extract_text_from_pdf(pdf_path: Path) -> str:
    """Extract text from PDF using PyMuPDF."""
    import fitz

    doc = fitz.open(str(pdf_path))
    pages_text: list[str] = []
    for page in doc:
        pages_text.append(page.get_text())
    doc.close()
    return "\n".join(pages_text)


def split_into_positions(text: str, group_code: str) -> list[dict]:
    """Split the full text into per-position sections."""
    lines = text.split("\n")
    positions: list[dict] = []
    current: dict | None = None
    general_lines: list[str] = []
    found_first_position = False

    for line in lines:
        match = POSITION_HEADER.match(line.strip())
        if match:
            group_from_header = match.group(1)
            pos_suffix = match.group(2)
            code = f"{group_from_header}{pos_suffix}"

            if current:
                current["content"] = "\n".join(current["_lines"])
                del current["_lines"]
                positions.append(current)

            found_first_position = True
            current = {
                "code": code,
                "group_code": group_code,
                "code_level": 2,  # position level
                "title": line.strip(),
                "_lines": [line],
            }
        elif current:
            current["_lines"].append(line)
        elif not found_first_position:
            general_lines.append(line)

    # Don't forget the last position
    if current:
        current["content"] = "\n".join(current["_lines"])
        del current["_lines"]
        positions.append(current)

    # Add general notes for the group
    if general_lines:
        general_text = "\n".join(general_lines).strip()
        if len(general_text) > 50:
            positions.insert(
                0,
                {
                    "code": group_code,
                    "group_code": group_code,
                    "code_level": 1,
                    "title": f"Общие положения к группе {group_code}",
                    "content": general_text,
                },
            )

    return positions


def split_into_sections(content: str) -> list[dict]:
    """Split position content into logical sections: general, includes, excludes.

    Returns list of {"section_type": str, "content": str} dicts.
    Handles multiple includes/excludes blocks by merging them.
    """
    # Find all marker positions
    markers: list[tuple[int, str]] = []

    for match in EXCLUDES_MARKERS.finditer(content):
        markers.append((match.start(), "excludes"))

    for match in INCLUDES_MARKERS.finditer(content):
        # Make sure this isn't inside an excludes marker ("не включаются" contains "включаются")
        # Check if there's a "не" before this match within 5 chars
        start = max(0, match.start() - 10)
        prefix = content[start:match.start()].lower()
        if "не " in prefix or "не\n" in prefix:
            continue
        markers.append((match.start(), "includes"))

    # Sort by position
    markers.sort(key=lambda x: x[0])

    if not markers:
        # No markers found — entire content is general
        return [{"section_type": "general", "content": content.strip()}]

    sections: dict[str, list[str]] = {
        "general": [],
        "includes": [],
        "excludes": [],
    }

    # Text before first marker is general
    first_pos = markers[0][0]
    general_text = content[:first_pos].strip()
    if general_text:
        sections["general"].append(general_text)

    # Process each marker section
    for i, (pos, section_type) in enumerate(markers):
        end_pos = markers[i + 1][0] if i + 1 < len(markers) else len(content)
        section_text = content[pos:end_pos].strip()
        if section_text:
            sections[section_type].append(section_text)

    result: list[dict] = []
    for section_type in ("general", "includes", "excludes"):
        texts = sections[section_type]
        if texts:
            merged = "\n\n".join(texts)
            result.append({"section_type": section_type, "content": merged})

    return result


def extract_references(text: str) -> list[str]:
    """Extract referenced position codes from excludes text."""
    refs = REFERENCE_PATTERN.findall(text)
    # Normalize: "85.02" -> "8502"
    return [r.replace(".", "") for r in refs]


def estimate_tokens(text: str) -> int:
    """Rough token estimation: ~4 chars per token for Russian text."""
    return len(text) // 4


def parse_all_pdfs() -> list[dict]:
    """Parse all available explanatory note PDFs into section-based records."""
    all_records: list[dict] = []

    pdf_files = sorted(PSN_DIR.glob("psn*.pdf"))
    if not pdf_files:
        print(f"No PDF files found in {PSN_DIR}")
        print("Run scripts/download_pdfs.py first")
        return []

    print(f"Found {len(pdf_files)} PDF files to parse")

    for pdf_path in pdf_files:
        group_code = pdf_path.stem.replace("psn", "")
        print(f"  Parsing {pdf_path.name} (group {group_code})...")

        try:
            text = extract_text_from_pdf(pdf_path)
        except Exception as e:
            print(f"    ERROR reading PDF: {e}")
            continue

        if len(text) < 100:
            print(f"    SKIP (too short: {len(text)} chars)")
            continue

        positions = split_into_positions(text, group_code)
        print(f"    Found {len(positions)} position blocks")

        record_count = 0
        for pos in positions:
            sections = split_into_sections(pos["content"])

            for section in sections:
                content = section["content"]
                if len(content) < 50:
                    continue

                record = {
                    "code": pos["code"],
                    "group_code": pos["group_code"],
                    "code_level": pos["code_level"],
                    "section_type": section["section_type"],
                    "title": pos.get("title", ""),
                    "content": content,
                    "content_tokens": estimate_tokens(content),
                    "source_file": pdf_path.name,
                }

                # Extract references from excludes sections
                if section["section_type"] == "excludes":
                    record["references_to"] = extract_references(content)

                all_records.append(record)
                record_count += 1

        print(f"    Produced {record_count} section records")

    print(f"\nTotal records: {len(all_records)}")
    return all_records


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    records = parse_all_pdfs()
    if records:
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        print(f"Written to {OUTPUT_PATH}")
    else:
        print("No records produced. Ensure PDFs are downloaded first.")


if __name__ == "__main__":
    main()
