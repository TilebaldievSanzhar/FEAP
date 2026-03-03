"""Seed excise rates for the Kyrgyz Republic (НК КР, ст. 336).

Based on: Закон КР №78 от 03.04.2023 (новая редакция базовых ставок акциза).

Usage:
    python scripts/seed_excise_rates.py

Idempotent: skips if excise_rates already has data.
Requires DATABASE_URL env var or .env file.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

LEGAL_REF = "НК КР, ст. 336 (Закон КР №78 от 03.04.2023)"

# Excise rates from Article 336 of the Tax Code of Kyrgyz Republic
# as amended by Law No. 78 of April 3, 2023.
#
# code_pattern works as a prefix match:
#   "2203" matches all codes starting with 2203

EXCISE_RATES: list[dict] = [
    # ── Спирт этиловый ──
    {
        "code_pattern": "2207",
        "product_name": "Спирт этиловый неденатурированный",
        "rate": "350",
        "rate_unit": "сом/литр",
        "legal_ref": LEGAL_REF,
        "note": None,
    },
    # ── Алкогольная продукция ──
    {
        "code_pattern": "220860",
        "product_name": "Водка",
        "rate": "300",
        "rate_unit": "сом/литр",
        "legal_ref": LEGAL_REF,
        "note": None,
    },
    {
        "code_pattern": "220830",
        "product_name": "Виски",
        "rate": "300",
        "rate_unit": "сом/литр",
        "legal_ref": LEGAL_REF,
        "note": None,
    },
    {
        "code_pattern": "220870",
        "product_name": "Ликёры",
        "rate": "300",
        "rate_unit": "сом/литр",
        "legal_ref": LEGAL_REF,
        "note": None,
    },
    {
        "code_pattern": "220890",
        "product_name": "Прочие спиртные напитки (в т.ч. слабоалкогольные)",
        "rate": "300",
        "rate_unit": "сом/литр",
        "legal_ref": LEGAL_REF,
        "note": "Для слабоалкогольных напитков — 200 сом/литр",
    },
    {
        "code_pattern": "220840",
        "product_name": "Вермут и прочие ароматизированные виноградные вина",
        "rate": "300",
        "rate_unit": "сом/литр",
        "legal_ref": LEGAL_REF,
        "note": None,
    },
    {
        "code_pattern": "220850",
        "product_name": "Крепленые напитки и бальзамы",
        "rate": "300",
        "rate_unit": "сом/литр",
        "legal_ref": LEGAL_REF,
        "note": None,
    },
    {
        "code_pattern": "220820",
        "product_name": "Коньяк",
        "rate": "200",
        "rate_unit": "сом/литр",
        "legal_ref": LEGAL_REF,
        "note": "Коды 2208 20 1200 — 2208 20 8900",
    },
    # ── Вино ──
    {
        "code_pattern": "2204",
        "product_name": "Вина виноградные натуральные",
        "rate": "100",
        "rate_unit": "сом/литр",
        "legal_ref": LEGAL_REF,
        "note": "Игристые вина и шампанское — 140 сом/литр",
    },
    {
        "code_pattern": "2205",
        "product_name": "Вермуты и прочие виноградные вина с добавками",
        "rate": "100",
        "rate_unit": "сом/литр",
        "legal_ref": LEGAL_REF,
        "note": None,
    },
    {
        "code_pattern": "2206",
        "product_name": "Напитки сброженные прочие (сидр, perry, мёд)",
        "rate": "100",
        "rate_unit": "сом/литр",
        "legal_ref": LEGAL_REF,
        "note": None,
    },
    {
        "code_pattern": "220430",
        "product_name": "Виноматериалы",
        "rate": "35",
        "rate_unit": "сом/литр",
        "legal_ref": LEGAL_REF,
        "note": None,
    },
    # ── Пиво ──
    {
        "code_pattern": "2203",
        "product_name": "Пиво солодовое",
        "rate": "30",
        "rate_unit": "сом/литр",
        "legal_ref": LEGAL_REF,
        "note": None,
    },
    # ── Безалкогольные напитки (подслащенные) ──
    {
        "code_pattern": "2202",
        "product_name": "Воды подслащенные и ароматизированные",
        "rate": "100",
        "rate_unit": "сом/литр",
        "legal_ref": LEGAL_REF,
        "note": "Национальные напитки (бозо, максым, жарма) — с 01.01.2027",
    },
    # ── Табачные изделия ──
    {
        "code_pattern": "2402",
        "product_name": "Сигареты, сигары, сигариллы",
        "rate": "3500",
        "rate_unit": "сом/1000 шт",
        "legal_ref": LEGAL_REF,
        "note": "Ставка на 2027 год. Для сигар — 350 сом/шт",
    },
    {
        "code_pattern": "2403",
        "product_name": "Табак трубочный, жевательный, нюхательный",
        "rate": "3000",
        "rate_unit": "сом/кг",
        "legal_ref": LEGAL_REF,
        "note": "Ставка на 2027 год",
    },
    {
        "code_pattern": "2404",
        "product_name": "Табак нагреваемый, никотиносодержащая жидкость",
        "rate": "5300",
        "rate_unit": "сом/1000 шт",
        "legal_ref": LEGAL_REF,
        "note": "Никотиновая жидкость — 23 сом/мл (на 2027). Одноразовые е-сигареты — 100 сом/шт",
    },
    # ── Нефтепродукты ──
    {
        "code_pattern": "271012",
        "product_name": "Бензин автомобильный",
        "rate": "10000",
        "rate_unit": "сом/тонну",
        "legal_ref": LEGAL_REF,
        "note": "Коды 2710 12 110 — 2710 19 150 0",
    },
    {
        "code_pattern": "27101929",
        "product_name": "Дизельное топливо",
        "rate": "2000",
        "rate_unit": "сом/тонну",
        "legal_ref": LEGAL_REF,
        "note": None,
    },
    {
        "code_pattern": "27101921",
        "product_name": "Топливо для реактивных двигателей",
        "rate": "2000",
        "rate_unit": "сом/тонну",
        "legal_ref": LEGAL_REF,
        "note": None,
    },
    {
        "code_pattern": "271019",
        "product_name": "Мазут топочный",
        "rate": "2000",
        "rate_unit": "сом/тонну",
        "legal_ref": LEGAL_REF,
        "note": "Мазут и прочие тяжёлые дистилляты",
    },
    # ── Электронные сигареты (устройства) ──
    {
        "code_pattern": "8543400000",
        "product_name": "Электронные сигареты (устройства)",
        "rate": "100",
        "rate_unit": "сом/шт",
        "legal_ref": LEGAL_REF,
        "note": None,
    },
    # ── Автомобили с двигателем >3000 см³ ──
    {
        "code_pattern": "870324",
        "product_name": "Автомобили легковые с объёмом двигателя более 3000 см³",
        "rate": "500",
        "rate_unit": "сом/см³ свыше 3000",
        "legal_ref": LEGAL_REF,
        "note": "Акциз взимается за каждый см³ свыше 3000",
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
        count = conn.execute(text("SELECT COUNT(*) FROM excise_rates")).scalar()

    if count and count > 0:
        print(f"excise_rates already contains {count} rows — skipping.")
        engine.dispose()
        return

    print(f"Loading {len(EXCISE_RATES)} excise rates...")
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO excise_rates
                    (code_pattern, product_name, rate, rate_unit, legal_ref, note)
                VALUES
                    (:code_pattern, :product_name, :rate, :rate_unit, :legal_ref, :note)
            """),
            EXCISE_RATES,
        )

    print(f"Done! Inserted {len(EXCISE_RATES)} excise rates.")
    engine.dispose()


if __name__ == "__main__":
    main()
