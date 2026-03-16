"""Seed NTM measure types (13 categories from TWS.BY R12 API).

These are reference data — the 13 standard types of non-tariff measures.

Usage:
    python scripts/seed_ntm_types.py

Idempotent: skips if ntm_measure_types already has data.
Requires DATABASE_URL env var or .env file.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# 13 measure types matching TWS.BY R12 API Kind values.
# Categories used for flag computation in NtmSummary.
NTM_MEASURE_TYPES: list[dict] = [
    {
        "kind": 1,
        "name_ru": "Запрет ввоза",
        "name_en": "Import prohibition",
        "category": "prohibition",
        "direction": "import",
    },
    {
        "kind": 2,
        "name_ru": "Ограничения ввоза",
        "name_en": "Import restrictions",
        "category": "prohibition",
        "direction": "import",
    },
    {
        "kind": 3,
        "name_ru": "Квоты",
        "name_en": "Quotas",
        "category": "quota",
        "direction": "both",
    },
    {
        "kind": 4,
        "name_ru": "Лицензирование",
        "name_en": "Licensing requirement",
        "category": "licensing",
        "direction": "both",
    },
    {
        "kind": 5,
        "name_ru": "Сертификация",
        "name_en": "Certification requirement",
        "category": "certification",
        "direction": "import",
    },
    {
        "kind": 6,
        "name_ru": "Соответствие техническим регламентам",
        "name_en": "Technical regulation compliance",
        "category": "technical_regulation",
        "direction": "import",
    },
    {
        "kind": 7,
        "name_ru": "Санитарно-эпидемиологический контроль",
        "name_en": "Sanitary measures",
        "category": "sanitary",
        "direction": "import",
    },
    {
        "kind": 8,
        "name_ru": "Ветеринарно-санитарный контроль",
        "name_en": "Veterinary-sanitary measures",
        "category": "veterinary",
        "direction": "import",
    },
    {
        "kind": 9,
        "name_ru": "Фитосанитарный контроль",
        "name_en": "Phytosanitary measures",
        "category": "phytosanitary",
        "direction": "import",
    },
    {
        "kind": 10,
        "name_ru": "Отсрочка/рассрочка НДС",
        "name_en": "VAT deferral eligibility",
        "category": "other",
        "direction": "import",
    },
    {
        "kind": 11,
        "name_ru": "Маркировка контрольными знаками",
        "name_en": "Control mark labeling",
        "category": "marking",
        "direction": "import",
    },
    {
        "kind": 12,
        "name_ru": "Маркировка средствами идентификации",
        "name_en": "ID marking",
        "category": "marking",
        "direction": "import",
    },
    {
        "kind": 13,
        "name_ru": "Предварительное информированное согласие",
        "name_en": "Prior informed consent",
        "category": "other",
        "direction": "import",
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
        count = conn.execute(text("SELECT COUNT(*) FROM ntm_measure_types")).scalar()

    if count and count > 0:
        print(f"ntm_measure_types already contains {count} rows — skipping.")
        engine.dispose()
        return

    print(f"Seeding {len(NTM_MEASURE_TYPES)} NTM measure types...")
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO ntm_measure_types (kind, name_ru, name_en, category, direction)
                VALUES (:kind, :name_ru, :name_en, :category, :direction)
            """),
            NTM_MEASURE_TYPES,
        )

    print("Done!")
    engine.dispose()


if __name__ == "__main__":
    main()
