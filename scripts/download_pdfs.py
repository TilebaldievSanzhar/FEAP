"""Download PDF files from ЕЭК (Eurasian Economic Commission).

Downloads:
  - Explanatory notes (пояснения): psn01.pdf .. psn97.pdf
  - OPI rules (правила): pravila.pdf

Source: https://eec.eaeunion.org/comission/department/catr/psn/

Usage:
    python scripts/download_pdfs.py
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PSN_DIR = PROJECT_ROOT / "data" / "pdfs" / "psn"
OPI_DIR = PROJECT_ROOT / "data" / "pdfs" / "opi"

BASE_URL = "https://eec.eaeunion.org/upload/files/catr/psn"

# Groups 01-97 (no group 98/99)
ALL_GROUPS = [f"{i:02d}" for i in range(1, 98)]


def download_file(url: str, dest: Path, timeout: float = 60.0) -> bool:
    """Download a file, return True on success."""
    if dest.exists() and dest.stat().st_size > 1000:
        print(f"  SKIP (exists): {dest.name}")
        return True

    try:
        with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as r:
            if r.status_code != 200:
                print(f"  FAIL ({r.status_code}): {url}")
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(8192):
                    f.write(chunk)
        size_kb = dest.stat().st_size / 1024
        print(f"  OK ({size_kb:.0f} KB): {dest.name}")
        return True
    except Exception as e:
        print(f"  ERROR: {url} -> {e}")
        return False


def download_explanatory_notes() -> None:
    """Download psn01.pdf through psn97.pdf."""
    print("Downloading explanatory notes (пояснения)...")
    PSN_DIR.mkdir(parents=True, exist_ok=True)

    ok, fail = 0, 0
    for group in ALL_GROUPS:
        url = f"{BASE_URL}/psn{group}.pdf"
        dest = PSN_DIR / f"psn{group}.pdf"
        if download_file(url, dest):
            ok += 1
        else:
            fail += 1
        time.sleep(0.5)  # Be polite to the server

    print(f"Explanatory notes: {ok} downloaded, {fail} failed")


def download_opi() -> None:
    """Download OPI rules PDF."""
    print("Downloading OPI rules (правила)...")
    OPI_DIR.mkdir(parents=True, exist_ok=True)

    url = f"{BASE_URL}/pravila.pdf"
    dest = OPI_DIR / "pravila.pdf"
    download_file(url, dest)


def main() -> None:
    download_explanatory_notes()
    download_opi()
    print("Done!")


if __name__ == "__main__":
    main()
