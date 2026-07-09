"""Seed классификаторов Решения КТС № 378 (минимальный набор для ИМ 40).

Usage:
    python scripts/seed/classifiers.py

Idempotent: пропускает, если ref_classifiers уже наполнена.

ВАЖНО про verified:
- verified=True  — код из международного стандарта (Incoterms/ТК ЕАЭС),
  расхождение с Решением 378 крайне маловероятно;
- verified=False — значение внесено из декларантской практики ЕАЭС и
  ОБЯЗАНО быть сверено с актуальной редакцией Решения 378 и реальной ДТ КР
  (TODO-verify, docs/dt_draft_spec.md §12.3). ФЛК использует только
  множество кодов; имена — для UI.

Страны и валюты сознательно НЕ сидируются: неполный список дал бы ложные
ошибки F-09, а формат (ISO alpha-2 / alpha-3) уже проверяется схемой и F-08.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

CLASSIFIERS: list[dict] = [
    # ── Условия поставки (графа 20) — Incoterms 2020, verified ──────────────
    *[
        {"classifier_code": "delivery_terms", "item_code": code,
         "name_ru": name, "verified": True}
        for code, name in [
            ("EXW", "Франко завод"),
            ("FCA", "Франко перевозчик"),
            ("FAS", "Свободно вдоль борта судна"),
            ("FOB", "Свободно на борту"),
            ("CFR", "Стоимость и фрахт"),
            ("CIF", "Стоимость, страхование и фрахт"),
            ("CPT", "Перевозка оплачена до"),
            ("CIP", "Перевозка и страхование оплачены до"),
            ("DAP", "Поставка в месте назначения"),
            ("DPU", "Поставка в месте назначения с разгрузкой"),
            ("DDP", "Поставка с оплатой пошлин"),
        ]
    ],
    # ── Виды транспорта (графы 25/26) — коды ЕЭК ООН/Решение 378 ────────────
    *[
        {"classifier_code": "transport_types", "item_code": code,
         "name_ru": name, "verified": True}
        for code, name in [
            ("10", "Морской транспорт"),
            ("20", "Железнодорожный транспорт"),
            ("30", "Автодорожный транспорт"),
            ("40", "Воздушный транспорт"),
            ("50", "Почтовое отправление"),
            ("71", "Трубопроводный транспорт"),
            ("72", "Линии электропередачи"),
            ("80", "Внутренний водный транспорт"),
            ("90", "Самоходом"),
        ]
    ],
    # ── Таможенные процедуры (графы 1/37) ────────────────────────────────────
    {"classifier_code": "customs_procedures", "item_code": "40",
     "name_ru": "Выпуск для внутреннего потребления", "verified": True},
    {"classifier_code": "customs_procedures", "item_code": "10",
     "name_ru": "Экспорт", "verified": True},
    {"classifier_code": "customs_procedures", "item_code": "00",
     "name_ru": "Предшествующая процедура отсутствует", "verified": True},
    # ── Методы определения таможенной стоимости (графа 43), гл. 5 ТК ЕАЭС ──
    *[
        {"classifier_code": "mos_methods", "item_code": str(n),
         "name_ru": name, "verified": True}
        for n, name in [
            (1, "По стоимости сделки с ввозимыми товарами"),
            (2, "По стоимости сделки с идентичными товарами"),
            (3, "По стоимости сделки с однородными товарами"),
            (4, "Метод вычитания"),
            (5, "Метод сложения"),
            (6, "Резервный метод"),
        ]
    ],
    # ── Характер сделки (графа 24) — TODO(verify) по реальной ДТ КР ─────────
    {"classifier_code": "deal_nature", "item_code": "010",
     "name_ru": "Возмездная поставка на условиях купли-продажи", "verified": False},
    # ── Виды документов (графа 44) — TODO(verify) по прил. 8 Решения 378 ────
    *[
        {"classifier_code": "doc_kinds", "item_code": code,
         "name_ru": name, "verified": False}
        for code, name in [
            ("02011", "Коносамент"),
            ("02013", "Железнодорожная накладная"),
            ("02015", "Международная автотранспортная накладная (CMR)"),
            ("02016", "Авианакладная (AWB)"),
            ("03011", "Договор (контракт) купли-продажи"),
            ("03012", "Изменения/дополнения к договору"),
            ("04021", "Счёт-фактура (инвойс) к договору"),
            ("06011", "Сертификат о происхождении товара (общая форма)"),
        ]
    ],
    # ── Виды платежей (графа 47) — TODO(verify), см. PAYMENT_CODES ──────────
    *[
        {"classifier_code": "payment_kinds", "item_code": code,
         "name_ru": name, "verified": False}
        for code, name in [
            ("1010", "Таможенные сборы за совершение таможенных операций"),
            ("2010", "Ввозная таможенная пошлина"),
            ("4010", "Акциз"),
            ("5010", "Налог на добавленную стоимость"),
        ]
    ],
]


def seed_classifiers(force: bool = False) -> None:
    from sqlalchemy import create_engine, text

    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://feap:feap@localhost:5432/feap",
    ).replace("+asyncpg", "")

    engine = create_engine(db_url)

    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM ref_classifiers")).scalar()

    if count and count > 0 and not force:
        print(f"ref_classifiers already contains {count} rows — skipping.")
        engine.dispose()
        return

    print(f"Seeding {len(CLASSIFIERS)} classifier values (Решение 378)...")
    with engine.begin() as conn:
        if force:
            conn.execute(text("DELETE FROM ref_classifiers"))
        conn.execute(
            text("""
                INSERT INTO ref_classifiers (classifier_code, item_code, name_ru, verified)
                VALUES (:classifier_code, :item_code, :name_ru, :verified)
            """),
            CLASSIFIERS,
        )

    unverified = sum(1 for c in CLASSIFIERS if not c["verified"])
    print(f"Done! ({unverified} значений с verified=false — сверить с Решением 378)")
    engine.dispose()


if __name__ == "__main__":
    seed_classifiers()
