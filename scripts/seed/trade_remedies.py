"""Загрузка мер защиты рынка ЕАЭС из data/trade_remedies_seed.json в trade_remedies.

Usage:
    python scripts/seed/trade_remedies.py

Идемпотентен: upsert по measure_no; меры, пропавшие из снимка, помечаются
status='expired' (не удаляются — нужны для аудита старых драфтов).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

SEED_PATH = PROJECT_ROOT / "data" / "trade_remedies_seed.json"


def seed_trade_remedies(path: Path = SEED_PATH) -> int:
    from sqlalchemy import create_engine, text

    if not path.exists():
        print(f"{path} not found — run scripts/fetch/eaeu_remedies.py first; skipping.")
        return 0
    seed = json.loads(path.read_text(encoding="utf-8"))
    measures = seed.get("measures") or []

    db_url = os.environ.get(
        "DATABASE_URL", "postgresql+psycopg2://feap:feap@localhost:5432/feap",
    ).replace("+asyncpg", "+psycopg2")
    engine = create_engine(db_url)
    with engine.begin() as conn:
        for m in measures:
            conn.execute(
                text("""
                    INSERT INTO trade_remedies
                        (measure_no, kind, title, product_description, countries, codes,
                         rates, notes, legal_basis, status, valid_from, valid_to,
                         needs_review, source_url, source_modified, updated_at)
                    VALUES
                        (:measure_no, :kind, :title, :product_description, :countries, :codes,
                         CAST(:rates AS JSONB), :notes, :legal_basis, :status,
                         :valid_from, :valid_to, :needs_review, :source_url,
                         :source_modified, now())
                    ON CONFLICT (measure_no) DO UPDATE SET
                        kind = EXCLUDED.kind, title = EXCLUDED.title,
                        product_description = EXCLUDED.product_description,
                        countries = EXCLUDED.countries, codes = EXCLUDED.codes,
                        rates = EXCLUDED.rates, notes = EXCLUDED.notes,
                        legal_basis = EXCLUDED.legal_basis, status = EXCLUDED.status,
                        valid_from = EXCLUDED.valid_from, valid_to = EXCLUDED.valid_to,
                        needs_review = EXCLUDED.needs_review,
                        source_url = EXCLUDED.source_url,
                        source_modified = EXCLUDED.source_modified,
                        updated_at = now()
                """),
                {
                    **{k: m.get(k) for k in (
                        "measure_no", "kind", "title", "product_description", "countries",
                        "codes", "notes", "legal_basis", "status", "valid_from", "valid_to",
                        "needs_review", "source_url", "source_modified",
                    )},
                    "rates": json.dumps(m.get("rates") or [], ensure_ascii=False),
                },
            )
        present = [m["measure_no"] for m in measures]
        conn.execute(
            text("UPDATE trade_remedies SET status = 'expired', updated_at = now() "
                 "WHERE NOT (measure_no = ANY(:present)) AND status <> 'expired'"),
            {"present": present},
        )
    engine.dispose()
    return len(measures)


if __name__ == "__main__":
    n = seed_trade_remedies()
    print(f"trade_remedies: {n} measures upserted")
