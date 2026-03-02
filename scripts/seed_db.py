"""Master script: load tree, OPI rules, and explanatory notes into PostgreSQL.

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
OPI_PATH = PROJECT_ROOT / "data" / "parsed" / "opi_rules.json"
EXPLANATORY_PATH = PROJECT_ROOT / "data" / "parsed" / "explanatory_notes.json"


def seed_tree(conn, nodes: list[dict]) -> None:
    from sqlalchemy import text

    conn.execute(text("TRUNCATE tnved_tree CASCADE"))

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

    conn.execute(text(f"SELECT setval('tnved_tree_id_seq', {len(nodes)})"))


def seed_opi(conn, rules: list[dict]) -> None:
    from sqlalchemy import text

    conn.execute(text("TRUNCATE tnved_opi"))
    conn.execute(
        text("""
            INSERT INTO tnved_opi
                (rule_number, rule_text, prompt_instruction, applicable_levels, priority)
            VALUES
                (:rule_number, :rule_text, :prompt_instruction, :applicable_levels, :priority)
        """),
        rules,
    )
    print(f"  Inserted {len(rules)} OPI rules")


def seed_explanatory_notes(conn, records: list[dict]) -> None:
    from sqlalchemy import text

    conn.execute(text("TRUNCATE tnved_explanatory_notes"))

    batch_size = 500
    for i in range(0, len(records), batch_size):
        batch = records[i : i + batch_size]
        conn.execute(
            text("""
                INSERT INTO tnved_explanatory_notes
                    (code, group_code, code_level, section_type, title,
                     content, content_tokens, source_file)
                VALUES
                    (:code, :group_code, :code_level, :section_type, :title,
                     :content, :content_tokens, :source_file)
            """),
            [
                {
                    "code": r["code"],
                    "group_code": r["group_code"],
                    "code_level": r["code_level"],
                    "section_type": r["section_type"],
                    "title": r.get("title", ""),
                    "content": r["content"],
                    "content_tokens": r.get("content_tokens"),
                    "source_file": r.get("source_file", ""),
                }
                for r in batch
            ],
        )
        print(f"  Inserted {min(i + batch_size, len(records))}/{len(records)}")


def main() -> None:
    from sqlalchemy import create_engine, text

    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://feap:feap@localhost:5432/feap",
    ).replace("+asyncpg", "")

    engine = create_engine(db_url)

    with engine.connect() as conn:
        tree_count = conn.execute(text("SELECT COUNT(*) FROM tnved_tree")).scalar()
        opi_count = conn.execute(text("SELECT COUNT(*) FROM tnved_opi")).scalar()

    if tree_count and tree_count > 0:
        print(f"tnved_tree already contains {tree_count} rows — skipping tree seed.")
    else:
        with open(TREE_PATH, encoding="utf-8") as f:
            nodes = json.load(f)
        print(f"Loading {len(nodes)} nodes into tnved_tree...")
        with engine.begin() as conn:
            seed_tree(conn, nodes)
        print("Tree done!")

    if opi_count and opi_count > 0:
        print(f"tnved_opi already contains {opi_count} rows — skipping OPI seed.")
    else:
        with open(OPI_PATH, encoding="utf-8") as f:
            rules = json.load(f)
        print(f"Loading {len(rules)} OPI rules into tnved_opi...")
        with engine.begin() as conn:
            seed_opi(conn, rules)
        print("OPI done!")

    # Explanatory notes (optional — only if parsed JSON exists)
    with engine.connect() as conn:
        expl_count = conn.execute(text("SELECT COUNT(*) FROM tnved_explanatory_notes")).scalar()

    if expl_count and expl_count > 0:
        print(f"tnved_explanatory_notes already contains {expl_count} rows — skipping.")
    elif EXPLANATORY_PATH.exists():
        with open(EXPLANATORY_PATH, encoding="utf-8") as f:
            records = json.load(f)
        print(f"Loading {len(records)} explanatory note sections into tnved_explanatory_notes...")
        with engine.begin() as conn:
            seed_explanatory_notes(conn, records)
        print("Explanatory notes done!")
    else:
        print(f"Explanatory notes JSON not found at {EXPLANATORY_PATH} — skipping.")
        print("Run: python scripts/parse_explanatory_pdfs.py first.")

    engine.dispose()


if __name__ == "__main__":
    main()
