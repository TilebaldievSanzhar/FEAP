"""Parse explanatory notes PDFs (пояснения) into structured chunks.

Reads PDFs from data/pdfs/psn/psn{01-97}.pdf
Produces data/parsed/explanatory_notes.json

Each PDF covers one group (e.g., psn85.pdf = group 85).
Structure inside PDF:
  - General notes for the group
  - Per-position explanations (XX.YY pattern like 85.01)
  - Includes/excludes sections
  - Examples and definitions

We parse into chunks suitable for embedding + RAG.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PSN_DIR = PROJECT_ROOT / "data" / "pdfs" / "psn"
OUTPUT_PATH = PROJECT_ROOT / "data" / "parsed" / "explanatory_notes.json"

# Regex patterns
POSITION_HEADER = re.compile(r"^(\d{2})\.(\d{2})\b")
INCLUDES_MARKER = re.compile(
    r"(В данную (?:позицию|субпозицию|товарную позицию|группу) включа(?:ют|е)ся)",
    re.IGNORECASE,
)
EXCLUDES_MARKER = re.compile(
    r"(В данную (?:позицию|субпозицию|товарную позицию|группу) не включа(?:ют|е)ся)",
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
    # Find all position headers
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
        if len(general_text) > 50:  # Skip near-empty general sections
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


def classify_sections(content: str) -> str:
    """Determine section type of content block."""
    if EXCLUDES_MARKER.search(content[:500]):
        return "excludes"
    if INCLUDES_MARKER.search(content[:500]):
        return "includes"
    return "general"


def chunk_text(text: str, max_tokens: int = 1200, overlap: int = 150) -> list[str]:
    """Split text into overlapping chunks.

    Uses rough token estimation: 1 token ≈ 4 chars for Russian text.
    """
    max_chars = max_tokens * 4
    overlap_chars = overlap * 4

    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + max_chars

        # Try to break at paragraph or sentence boundary
        if end < len(text):
            # Look for paragraph break
            para_break = text.rfind("\n\n", start + max_chars // 2, end)
            if para_break > start:
                end = para_break
            else:
                # Look for sentence break
                sent_break = text.rfind(". ", start + max_chars // 2, end)
                if sent_break > start:
                    end = sent_break + 1

        chunks.append(text[start:end].strip())
        start = end - overlap_chars

    return [c for c in chunks if len(c) > 20]


def parse_all_pdfs() -> list[dict]:
    """Parse all available explanatory note PDFs."""
    all_chunks: list[dict] = []

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
        print(f"    Found {len(positions)} position sections")

        for pos in positions:
            section_type = classify_sections(pos["content"])
            chunks = chunk_text(pos["content"])

            for i, chunk in enumerate(chunks):
                all_chunks.append({
                    "code": pos["code"],
                    "code_level": pos["code_level"],
                    "section_type": section_type,
                    "content": pos["content"] if i == 0 else "",  # Full content only in first chunk
                    "chunk_index": i,
                    "chunk_text": chunk,
                    "source_file": pdf_path.name,
                })

    print(f"\nTotal chunks: {len(all_chunks)}")
    return all_chunks


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    chunks = parse_all_pdfs()
    if chunks:
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(chunks, f, ensure_ascii=False, indent=2)
        print(f"Written to {OUTPUT_PATH}")
    else:
        print("No chunks produced. Ensure PDFs are downloaded first.")


if __name__ == "__main__":
    main()
