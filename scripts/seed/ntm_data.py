"""Seed NTM measures and code bindings from data/ntm_seed.json.

Usage:
    python scripts/seed/ntm_data.py [--force]

    --force: drop existing measures and re-seed (otherwise skip if data exists)
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

# ── Setup path ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from feap.config import settings  # noqa: E402
from feap.db.models import Base, NtmMeasure, NtmMeasureCode, NtmMeasureType  # noqa: E402

SEED_FILE = ROOT / "data" / "ntm_seed.json"


def get_engine():
    url = settings.database_url_sync
    return sa.create_engine(url)


def seed_ntm_data(force: bool = False) -> None:
    engine = get_engine()

    with Session(engine) as session:
        # Check if measures already exist
        count = session.execute(
            sa.select(sa.func.count()).select_from(NtmMeasure)
        ).scalar() or 0

        if count > 0 and not force:
            print(f"ntm_measures already has {count} rows — skipping (use --force to re-seed)")
            return

        if count > 0 and force:
            print(f"Dropping {count} existing measures...")
            session.execute(sa.delete(NtmMeasureCode))
            session.execute(sa.delete(NtmMeasure))
            session.commit()

        # Load seed data
        with open(SEED_FILE, encoding="utf-8") as f:
            data = json.load(f)

        # Build kind -> measure_type_id map
        types = session.execute(sa.select(NtmMeasureType)).scalars().all()
        kind_to_id = {t.kind: t.id for t in types}
        if not kind_to_id:
            print("ERROR: ntm_measure_types is empty — run seed_ntm_types.py first")
            return

        measures_created = 0
        codes_created = 0

        for entry in data["measures"]:
            kind = entry["kind"]
            type_id = kind_to_id.get(kind)
            if type_id is None:
                print(f"  WARNING: Unknown kind={kind} for '{entry['name']}' — skipping")
                continue

            measure = NtmMeasure(
                measure_type_id=type_id,
                name=entry["name"],
                legal_basis=entry.get("legal_basis"),
                valid_from=date.fromisoformat(entry["valid_from"]),
                scope="eaeu",
                data_source="manual",
            )
            session.add(measure)
            session.flush()  # get ID
            measures_created += 1

            for code_entry in entry.get("codes", []):
                code_val = code_entry["code"].replace(" ", "")
                # Determine code_level from code length
                code_len = len(code_val)
                if code_len <= 2:
                    code_level = 1  # group
                elif code_len <= 4:
                    code_level = 2  # position
                elif code_len <= 6:
                    code_level = 3  # subposition
                else:
                    code_level = 4  # leaf

                mc = NtmMeasureCode(
                    measure_id=measure.id,
                    code=code_val,
                    code_level=code_level,
                    product_description=code_entry.get("description"),
                    applies_to_children=code_entry.get("applies_to_children", True),
                )
                session.add(mc)
                codes_created += 1

        session.commit()
        print(f"Seeded {measures_created} measures with {codes_created} code bindings")


if __name__ == "__main__":
    force = "--force" in sys.argv
    seed_ntm_data(force=force)
