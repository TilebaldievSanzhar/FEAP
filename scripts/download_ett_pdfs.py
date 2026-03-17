"""Download ETT nomenclature PDFs from EEC (Eurasian Economic Commission).

Downloads 96 PDF files (one per ТН ВЭД group, 01-97 excluding 77)
into data/ett_pdfs/.

Source: https://eec.eaeunion.org/comission/department/catr/ett/

Usage:
    python scripts/download_ett_pdfs.py
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ETT_DIR = PROJECT_ROOT / "data" / "ett_pdfs"

# Hardcoded URLs scraped from EEC page (stable since 2022, updated periodically)
ETT_URLS: dict[str, str] = {
    "01": "https://eec.eaeunion.org/upload/files/catr/ett/ru.01_2022_25.04.2022.pdf",
    "02": "https://eec.eaeunion.org/upload/files/catr/ett/ru.02_2022.pdf",
    "03": "https://eec.eaeunion.org/upload/files/catr/ett/ru.03_2022_28.03.2022.pdf",
    "04": "https://eec.eaeunion.org/upload/files/catr/ett/ru.04_2022_25.04.2022.pdf",
    "05": "https://eec.eaeunion.org/upload/files/catr/ett/ru.05_2022_28.04.2022.pdf",
    "06": "https://eec.eaeunion.org/upload/files/catr/ett/ru.06_2022_25.04.2022.pdf",
    "07": "https://eec.eaeunion.org/upload/files/catr/ett/ru.07_2022_08.05.2022.pdf",
    "08": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.08_2022_28.07.2024.pdf",
    "09": "https://eec.eaeunion.org/upload/files/catr/ett/ru.09_2022_25.04.2022.pdf",
    "10": "https://eec.eaeunion.org/upload/files/catr/ett/ru.10_2022_25.04.2022.pdf",
    "11": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.11_2022_08.03.2026.pdf",
    "12": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.12_2022_08.03.2026.pdf",
    "13": "https://eec.eaeunion.org/upload/files/catr/ett/ru.13_2022_25.04.2022.pdf",
    "14": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.14_2022_08.03.2026.pdf",
    "15": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.15_2022_08.03.2026.pdf",
    "16": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.16_2022_10.09.2023.pdf",
    "17": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.17_2022_08.03.2026.pdf",
    "18": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.18_2022_08.03.2026.pdf",
    "19": "https://eec.eaeunion.org/upload/files/catr/ett/ru.19_2022_25.04.2022.pdf",
    "20": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.20_2022_12.06.2025.pdf",
    "21": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.21_2022_08.03.2026.pdf",
    "22": "https://eec.eaeunion.org/upload/files/catr/ett/ru.22_2022_25.04.2022.pdf",
    "23": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.23_2022_19.02.2023.pdf",
    "24": "https://eec.eaeunion.org/upload/files/catr/ett/ru.24_2022.pdf",
    "25": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.25_2022_08.03.2026.pdf",
    "26": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.26_2022_20.11.2024.pdf",
    "27": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.27_2022_08.03.2026.pdf",
    "28": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.28_2022_08.03.2026.pdf",
    "29": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.29_2022_08.03.2026.pdf",
    "30": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.30_2022_08.03.2026.pdf",
    "31": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.31_2022_03.12.2023.pdf",
    "32": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.32_2022_08.03.2026.pdf",
    "33": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.33_2022_08.03.2026.pdf",
    "34": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.34_2022_08.03.2026.pdf",
    "35": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.35_2022_08.03.2026.pdf",
    "36": "https://eec.eaeunion.org/upload/files/catr/ett/ru.36_2022.pdf",
    "37": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.37_2022_08.03.2026.pdf",
    "38": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.38_2022_08.03.2026.pdf",
    "39": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.39_2022_08.03.2026.pdf",
    "40": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.40_2022_08.03.2026.pdf",
    "41": "https://eec.eaeunion.org/upload/files/catr/ett/ru.41_2022.pdf",
    "42": "https://eec.eaeunion.org/upload/files/catr/ett/ru.42_2022.pdf",
    "43": "https://eec.eaeunion.org/upload/files/catr/ett/ru.43_2022_25.04.2022.pdf",
    "44": "https://eec.eaeunion.org/upload/files/catr/ett/ru.44_2022_16.06.2022.pdf",
    "45": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.45_2022_08.03.2026.pdf",
    "46": "https://eec.eaeunion.org/upload/files/catr/ett/ru.46_2022.pdf",
    "47": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.47_2022_08.03.2026.pdf",
    "48": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.48_2022_08.03.2026.pdf",
    "49": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.49_2022_08.03.2026.pdf",
    "50": "https://eec.eaeunion.org/upload/files/catr/ett/ru.50_2022.pdf",
    "51": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.51_2022_07.02.2026.pdf",
    "52": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.52_2022_08.03.2026.pdf",
    "53": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.53_2022_08.03.2026.pdf",
    "54": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.54_2022_08.03.2026.pdf",
    "55": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.55_2022_08.03.2026.pdf",
    "56": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.56_2022_08.03.2026.pdf",
    "57": "https://eec.eaeunion.org/upload/files/catr/ett/ru.57_2022.pdf",
    "58": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.58_2022_08.03.2026.pdf",
    "59": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.59_2022_08.03.2026.pdf",
    "60": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.60_2022_08.03.2026.pdf",
    "61": "https://eec.eaeunion.org/upload/files/catr/ett/ru.61_2022_25.04.2022.pdf",
    "62": "https://eec.eaeunion.org/upload/files/catr/ett/ru.62_2022.pdf",
    "63": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.63_2022_12.06.2025.pdf",
    "64": "https://eec.eaeunion.org/upload/files/catr/ett/ru.64_2022.pdf",
    "65": "https://eec.eaeunion.org/upload/files/catr/ett/ru.65_2022.pdf",
    "66": "https://eec.eaeunion.org/upload/files/catr/ett/ru.66_2022.pdf",
    "67": "https://eec.eaeunion.org/upload/files/catr/ett/ru.67_2022.pdf",
    "68": "https://eec.eaeunion.org/upload/files/catr/ett/ru.68_2022_25.04.2022.pdf",
    "69": "https://eec.eaeunion.org/upload/files/catr/ett/ru.69_2022_08.05.2022.pdf",
    "70": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.70_2022_17.03.2024.pdf",
    "71": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.71_2022_25.01.2026.pdf",
    "72": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.72_2022_08.03.2026.pdf",
    "73": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.73_2022_08.03.2026.pdf",
    "74": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.74_2022_08.03.2026.pdf",
    "75": "https://eec.eaeunion.org/upload/files/catr/ett/ru.75_2022_25.04.2022.pdf",
    "76": "https://eec.eaeunion.org/upload/files/catr/ett/ru.76_2022_08.05.2022.pdf",
    "78": "https://eec.eaeunion.org/upload/files/catr/ett/ru.78_2022_25.04.2022.pdf",
    "79": "https://eec.eaeunion.org/upload/files/catr/ett/ru.79_2022.pdf",
    "80": "https://eec.eaeunion.org/upload/files/catr/ett/ru.80_2022.pdf",
    "81": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.81_2022_08.03.2026.pdf",
    "82": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.82_2022_08.03.2026.pdf",
    "83": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.83_2022_08.03.2026.pdf",
    "84": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.84_2022_08.03.2026.pdf",
    "85": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.85_2022_08.03.2026.pdf",
    "86": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.86_2022_20.03.2023.pdf",
    "87": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.87_2022_22.01.2026.pdf",
    "88": "https://eec.eaeunion.org/upload/files/catr/ett/ru.88_2022.pdf",
    "89": "https://eec.eaeunion.org/upload/files/catr/ett/ru.89_2022.pdf",
    "90": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.90_2022_08.03.2026.pdf",
    "91": "https://eec.eaeunion.org/upload/files/catr/ett/ru.91_2022.pdf",
    "92": "https://eec.eaeunion.org/upload/files/catr/ett/ru.92_2022.pdf",
    "93": "https://eec.eaeunion.org/upload/files/catr/ett/ru.93_2022.pdf",
    "94": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.94_2022_08.03.2026.pdf",
    "95": "https://eec.eaeunion.org/upload/files/catr/ett/ru.95_2022.pdf",
    "96": "https://eec.eaeunion.org/comission/department/catr/ett/ru.2022/ru.96_2022_08.03.2026.pdf",
    "97": "https://eec.eaeunion.org/upload/files/catr/ett/ru.97_2022.pdf",
}


def download_file(url: str, dest: Path, timeout: float = 120.0) -> bool:
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


def main() -> None:
    ETT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {len(ETT_URLS)} ETT nomenclature PDFs...")

    ok, fail = 0, 0
    for group_code, url in sorted(ETT_URLS.items()):
        dest = ETT_DIR / f"ru.{group_code}.pdf"
        if download_file(url, dest):
            ok += 1
        else:
            fail += 1
        time.sleep(0.3)

    print(f"\nResult: {ok} downloaded, {fail} failed")
    if fail:
        print("Re-run the script to retry failed downloads.")


if __name__ == "__main__":
    main()
