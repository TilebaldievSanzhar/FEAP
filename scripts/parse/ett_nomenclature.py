"""Parse ETT nomenclature PDFs into a single nomenclature.json.

Reads: data/ett_pdfs/ru.{01..97}.pdf (96 files, excluding group 77)
Produces: data/parsed/nomenclature.json

Names come from the name column of the official EEC group PDFs, read by text
coordinates (``feap.tariff.ett_names``): codes stand only in the code column,
table rows without a code start with dashes at the left edge of the name
column, wrapped lines are indented. Every leaf keeps its full path below the
heading — unnumbered rows («– – более 3 мас.%») and numbered intermediate rows
alike — so siblings stay distinguishable and conditions are not lost.

PyMuPDF is required (present in the Docker image; blocked in the local Windows
venv):

    docker run --rm --entrypoint python -v <repo>:/repo -w /repo \\
        -e PYTHONPATH=/repo/src:/repo feap-app scripts/parse/ett_nomenclature.py
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from feap.tariff.ett import EttParseError, extract_spans
from feap.tariff.ett_names import parse_group_names

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ETT_DIR = PROJECT_ROOT / "data" / "ett_pdfs"
OUTPUT_PATH = PROJECT_ROOT / "data" / "parsed" / "nomenclature.json"

ALL_GROUPS = [f"{i:02d}" for i in range(1, 98) if i != 77]


def extract_group_name(pdf: bytes) -> str:
    """Group title from the first page: uppercase lines after «ГРУППА NN»."""
    import pymupdf  # noqa: PLC0415 — heavy dependency, only for PDF parsing

    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        lines = doc[0].get_text().split("\n")

    collecting = False
    parts: list[str] = []
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


def parse_group_pdf(pdf_path: Path, group_code: str) -> dict:
    """One group PDF → names of its positions, subpositions and leaves."""
    raw = pdf_path.read_bytes()
    names = parse_group_names(extract_spans(raw), group_code)
    return {
        "group_name": extract_group_name(raw),
        "positions": names.positions,
        "subpositions": names.subpositions,
        "leaves": names.leaves,
        "issues": names.issues,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def parse_all() -> dict:
    """Parse all 96 group PDFs into unified nomenclature; refuse inconsistent trees."""
    result = {
        "meta": {
            "source": "eec.eaeunion.org",
            "parser": "feap.tariff.ett_names",
            "parsed_at": datetime.now(timezone.utc).isoformat(),
            "files_count": 0,
            "files": {},
        },
        "groups": {},
        "positions": {},
        "subpositions": {},
        "leaves": {},
    }
    issues: list[str] = []

    for group_code in ALL_GROUPS:
        pdf_path = ETT_DIR / f"ru.{group_code}.pdf"
        if not pdf_path.exists():
            print(f"  MISSING: {pdf_path.name}")
            continue

        data = parse_group_pdf(pdf_path, group_code)
        issues += [f"{group_code}: {issue}" for issue in data["issues"]]

        result["groups"][group_code] = {"name": data["group_name"]}
        result["positions"].update(data["positions"])
        result["subpositions"].update(data["subpositions"])
        result["leaves"].update(data["leaves"])
        result["meta"]["files"][group_code] = {"file": pdf_path.name, "sha256": data["sha256"]}
        result["meta"]["files_count"] += 1

        n_virt = sum(1 for sp in data["subpositions"].values() if sp["virtual_parents"])
        print(
            f"  {group_code}: {len(data['positions'])} pos, {len(data['subpositions'])} subpos "
            f"({n_virt} with virtual parents), {len(data['leaves'])} leaves"
        )

    if issues:
        raise EttParseError("Dash hierarchy is inconsistent:\n" + "\n".join(issues))
    return result


def print_stats(nom: dict) -> None:
    print("\nNomenclature statistics:")
    print(f"  Groups:       {len(nom['groups'])}")
    print(f"  Positions:    {len(nom['positions'])}")
    print(f"  Subpositions: {len(nom['subpositions'])}")
    print(f"  Leaves:       {len(nom['leaves'])}")

    by_position: dict[str, list[str]] = {}
    for code, sp in nom["subpositions"].items():
        by_position.setdefault(sp["position"], []).append(sp["full_name"])
    dup_sub = sum(1 for names in by_position.values() if len(names) != len(set(names)))

    by_subposition: dict[str, list[str]] = {}
    for leaf in nom["leaves"].values():
        by_subposition.setdefault(leaf["subposition"], []).append(leaf["full_name"])
    dup_leaf = sum(
        n - 1 for names in by_subposition.values() for n in Counter(names).values() if n > 1
    )
    print(f"  Positions with duplicate subposition names: {dup_sub}")
    print(f"  Leaves indistinguishable from a sibling:     {dup_leaf}")


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
