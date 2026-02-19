"""Generate embeddings for explanatory note chunks using Voyage AI.

Reads data/parsed/explanatory_notes.json
Updates the chunks with embeddings and produces data/parsed/explanatory_notes_with_embeddings.json

Alternatively, can write directly to PostgreSQL if DB is available.

Usage:
    python scripts/generate_embeddings.py [--batch-size 128] [--db]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_PATH = PROJECT_ROOT / "data" / "parsed" / "explanatory_notes.json"
OUTPUT_PATH = PROJECT_ROOT / "data" / "parsed" / "explanatory_notes_with_embeddings.json"


def generate_embeddings_voyage(
    texts: list[str],
    api_key: str,
    model: str = "voyage-3",
    batch_size: int = 128,
) -> list[list[float]]:
    """Generate embeddings using Voyage AI API via httpx."""
    import httpx

    client = httpx.Client(
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=60.0,
    )
    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        print(f"  Embedding batch {i // batch_size + 1}/{(len(texts) - 1) // batch_size + 1} "
              f"({len(batch)} texts)...")

        resp = client.post(
            "https://api.voyageai.com/v1/embeddings",
            json={"input": batch, "model": model, "input_type": "document"},
        )
        resp.raise_for_status()
        data = resp.json()
        all_embeddings.extend(item["embedding"] for item in data["data"])
        time.sleep(0.1)

    client.close()
    return all_embeddings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--db", action="store_true", help="Write directly to DB")
    args = parser.parse_args()

    api_key = os.environ.get("VOYAGE_API_KEY", "")
    if not api_key:
        print("ERROR: VOYAGE_API_KEY not set. Set it in .env or environment.")
        sys.exit(1)

    with open(INPUT_PATH, encoding="utf-8") as f:
        chunks = json.load(f)

    print(f"Loaded {len(chunks)} chunks")

    # Extract texts for embedding
    texts = [c["chunk_text"] for c in chunks]

    print(f"Generating embeddings for {len(texts)} chunks...")
    embeddings = generate_embeddings_voyage(texts, api_key, batch_size=args.batch_size)

    print(f"Generated {len(embeddings)} embeddings (dim={len(embeddings[0])})")

    # Attach embeddings to chunks
    for chunk, emb in zip(chunks, embeddings):
        chunk["embedding"] = emb

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False)
    print(f"Written to {OUTPUT_PATH}")

    if args.db:
        print("Writing to DB...")
        write_to_db(chunks)


def write_to_db(chunks: list[dict]) -> None:
    """Write chunks with embeddings to PostgreSQL."""
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

    from sqlalchemy import create_engine, text

    db_url = os.environ.get(
        "DATABASE_URL", "postgresql://feap:feap@localhost:5432/feap"
    ).replace("+asyncpg", "")

    engine = create_engine(db_url)

    with engine.begin() as conn:
        conn.execute(text("TRUNCATE tnved_explanatory_notes"))

        for i, chunk in enumerate(chunks):
            embedding_str = "[" + ",".join(str(x) for x in chunk["embedding"]) + "]"
            conn.execute(
                text("""
                    INSERT INTO tnved_explanatory_notes
                        (code, code_level, section_type, content, chunk_index,
                         chunk_text, embedding, source_file)
                    VALUES
                        (:code, :code_level, :section_type, :content, :chunk_index,
                         :chunk_text, :embedding::vector, :source_file)
                """),
                {
                    "code": chunk["code"],
                    "code_level": chunk["code_level"],
                    "section_type": chunk["section_type"],
                    "content": chunk.get("content", ""),
                    "chunk_index": chunk["chunk_index"],
                    "chunk_text": chunk["chunk_text"],
                    "embedding": embedding_str,
                    "source_file": chunk.get("source_file"),
                },
            )

            if (i + 1) % 500 == 0:
                print(f"  Inserted {i + 1}/{len(chunks)}")

    print(f"  Inserted {len(chunks)} chunks into DB")
    engine.dispose()


if __name__ == "__main__":
    main()
