"""Загрузка торговых режимов ЕАЭС из data/trade_agreements_seed.json.

Usage:
    python scripts/seed/trade_agreements.py

Идемпотентен: upsert по (country, agreement).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

SEED_PATH = PROJECT_ROOT / "data" / "trade_agreements_seed.json"

_FIELDS = (
    "country", "regime", "agreement", "legal_ref", "status", "in_force_from",
    "in_force_to", "origin_proof", "note", "source_url", "verified",
)


def seed_trade_agreements(path: Path = SEED_PATH) -> int:
    from sqlalchemy import create_engine, text

    rows = json.loads(path.read_text(encoding="utf-8")).get("agreements") or []
    db_url = os.environ.get(
        "DATABASE_URL", "postgresql+psycopg2://feap:feap@localhost:5432/feap",
    ).replace("+asyncpg", "+psycopg2")
    engine = create_engine(db_url)
    with engine.begin() as conn:
        for row in rows:
            conn.execute(
                text("""
                    INSERT INTO trade_agreements
                        (country, regime, agreement, legal_ref, status, in_force_from,
                         in_force_to, origin_proof, note, source_url, verified, updated_at)
                    VALUES
                        (:country, :regime, :agreement, :legal_ref, :status, :in_force_from,
                         :in_force_to, :origin_proof, :note, :source_url, :verified, now())
                    ON CONFLICT (country, agreement) DO UPDATE SET
                        regime = EXCLUDED.regime, legal_ref = EXCLUDED.legal_ref,
                        status = EXCLUDED.status, in_force_from = EXCLUDED.in_force_from,
                        in_force_to = EXCLUDED.in_force_to,
                        origin_proof = EXCLUDED.origin_proof, note = EXCLUDED.note,
                        source_url = EXCLUDED.source_url, verified = EXCLUDED.verified,
                        updated_at = now()
                """),
                {k: row.get(k) for k in _FIELDS},
            )
    engine.dispose()
    return len(rows)


if __name__ == "__main__":
    n = seed_trade_agreements()
    print(f"trade_agreements: {n} rows upserted")
