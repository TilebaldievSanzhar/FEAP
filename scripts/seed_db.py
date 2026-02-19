"""Master script: load tree.json into PostgreSQL tnved_tree table.

Usage:
    python scripts/seed_db.py

Requires DATABASE_URL env var or .env file.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Add src to path for feap imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

TREE_PATH = PROJECT_ROOT / "data" / "parsed" / "tree.json"


def main() -> None:
    # Use sync psycopg2/asyncpg is not needed for bulk insert
    import sqlalchemy
    from sqlalchemy import create_engine, text

    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://feap:feap@localhost:5432/feap",
    ).replace("+asyncpg", "")

    engine = create_engine(db_url)

    with open(TREE_PATH, encoding="utf-8") as f:
        nodes = json.load(f)

    print(f"Loading {len(nodes)} nodes into tnved_tree...")

    with engine.begin() as conn:
        # Clear existing data
        conn.execute(text("TRUNCATE tnved_tree CASCADE"))

        # Bulk insert in batches
        batch_size = 1000
        for i in range(0, len(nodes), batch_size):
            batch = nodes[i : i + batch_size]
            conn.execute(
                text("""
                    INSERT INTO tnved_tree
                        (id, code, code_formatted, level, parent_id, name, name_full,
                         section_roman, group_code, duty_rate, is_leaf)
                    VALUES
                        (:id, :code, :code_formatted, :level, :parent_id, :name, :name_full,
                         :section_roman, :group_code, :duty_rate, :is_leaf)
                """),
                [
                    {
                        "id": n["id"],
                        "code": n["code"],
                        "code_formatted": n["code_formatted"],
                        "level": n["level"],
                        "parent_id": n["parent_id"],
                        "name": n["name"],
                        "name_full": n["name_full"],
                        "section_roman": n["section_roman"],
                        "group_code": n["group_code"],
                        "duty_rate": n["duty_rate"],
                        "is_leaf": n["is_leaf"],
                    }
                    for n in batch
                ],
            )
            print(f"  Inserted {min(i + batch_size, len(nodes))}/{len(nodes)}")

        # Reset sequence
        conn.execute(text(f"SELECT setval('tnved_tree_id_seq', {len(nodes)})"))

    print("Done!")
    engine.dispose()


if __name__ == "__main__":
    main()
