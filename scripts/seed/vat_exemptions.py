"""Seed VAT exemptions for the Kyrgyz Republic (НК КР, ст. 289).

Usage:
    python scripts/seed/vat_exemptions.py

Idempotent: skips if vat_exemptions already has data.
Requires DATABASE_URL env var or .env file.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# Initial VAT exemptions based on Article 289 of the Tax Code of Kyrgyz Republic.
#
# NOTE: This is a starting set of stable exemptions.
# Temporary government decisions (e.g., flour, vegetable oil) should be added
# manually as they are published, with valid_from/valid_to dates.
#
# code_pattern works as a prefix match:
#   "3004" matches all codes starting with 3004 (e.g., 3004201000, 3004901000, etc.)

EXEMPTIONS: list[dict] = [
    # ── Лекарственные средства (ст. 289 п.12 НК КР) ──
    {
        "code_pattern": "3001",
        "exemption_type": "full",
        "rate_override": "0%",
        "legal_ref": "НК КР, ст. 289 п.12",
        "note": "Железы и прочие органы для органотерапевтических целей",
    },
    {
        "code_pattern": "3002",
        "exemption_type": "full",
        "rate_override": "0%",
        "legal_ref": "НК КР, ст. 289 п.12",
        "note": "Кровь человеческая; сыворотки, вакцины, токсины",
    },
    {
        "code_pattern": "3003",
        "exemption_type": "full",
        "rate_override": "0%",
        "legal_ref": "НК КР, ст. 289 п.12",
        "note": "Лекарственные средства (несфасованные)",
    },
    {
        "code_pattern": "3004",
        "exemption_type": "full",
        "rate_override": "0%",
        "legal_ref": "НК КР, ст. 289 п.12",
        "note": "Лекарственные средства (расфасованные, для розничной продажи)",
    },
    {
        "code_pattern": "3005",
        "exemption_type": "full",
        "rate_override": "0%",
        "legal_ref": "НК КР, ст. 289 п.12",
        "note": "Вата, марля, бинты и аналогичные изделия медицинские",
    },
    {
        "code_pattern": "3006",
        "exemption_type": "full",
        "rate_override": "0%",
        "legal_ref": "НК КР, ст. 289 п.12",
        "note": "Фармацевтическая продукция прочая",
    },
    # ── Протезно-ортопедические изделия, товары для инвалидов ──
    {
        "code_pattern": "9021",
        "exemption_type": "full",
        "rate_override": "0%",
        "legal_ref": "НК КР, ст. 289 п.18",
        "note": "Приспособления ортопедические, протезы, слуховые аппараты",
    },
    {
        "code_pattern": "8713",
        "exemption_type": "full",
        "rate_override": "0%",
        "legal_ref": "НК КР, ст. 289 п.18",
        "note": "Коляски инвалидные",
    },
]


def main() -> None:
    from sqlalchemy import create_engine, text

    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://feap:feap@localhost:5432/feap",
    ).replace("+asyncpg", "")

    engine = create_engine(db_url)

    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM vat_exemptions")).scalar()

    if count and count > 0:
        print(f"vat_exemptions already contains {count} rows — skipping.")
        engine.dispose()
        return

    print(f"Loading {len(EXEMPTIONS)} VAT exemptions...")
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO vat_exemptions
                    (code_pattern, exemption_type, rate_override, legal_ref, note)
                VALUES
                    (:code_pattern, :exemption_type, :rate_override, :legal_ref, :note)
            """),
            EXEMPTIONS,
        )

    print(f"Done! Inserted {len(EXEMPTIONS)} exemptions.")
    engine.dispose()


if __name__ == "__main__":
    main()
