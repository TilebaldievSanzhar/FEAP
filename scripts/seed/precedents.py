"""Реестр предварительных решений ЕАЭС → classification_precedents (feap.precedents).

Источник — снимок «Сборника принятых предварительных решений таможенных органов
государств — членов ЕАЭС по классификации товаров» (ЕС 999, справочник 1999 НСИ
ЕАЭС): каталог с manifest.json и страницами page_*.json, как его сохраняет
``python -m benchmark.scripts.collect_eaeu_predecisions --date <день>``.

Загружаются записи с кодом действующего листа номенклатуры и непустым описанием;
upsert по id записи. После загрузки процесс приложения перестраивает индекс сам
при следующем запуске (индекс строится один раз на процесс).

Usage:
    python scripts/seed/precedents.py benchmark/data/raw/eaeu_predecisions/2026-10-18
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

COUNTRIES = {"КАЗАХСТАН": "KZ", "РОССИЯ": "RU", "БЕЛАРУСЬ": "BY", "КЫРГЫЗСТАН": "KG", "АРМЕНИЯ": "AM"}


def snapshot_records(path: Path) -> tuple[list[dict], dict]:
    """Записи снимка; страница, изменённая после скачивания, — ошибка."""
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    records: list[dict] = []
    for page in manifest["pages"]:
        raw = (path / page["file"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != page["sha256"]:
            raise SystemExit(f"{page['file']} изменён после скачивания")
        records.extend(json.loads(raw))
    return records, manifest


def rows(records: list[dict], leaves: set[str], snapshot: dt.date) -> tuple[list[dict], dict[str, int]]:
    out, skipped = [], {"code not a current leaf": 0, "empty description": 0}
    for record in records:
        data = record.get("data") or {}
        code = re.sub(r"\D", "", data.get("CodeTNVED") or "")
        description = re.sub(r"\s+", " ", data.get("Description") or "").strip()
        if code not in leaves:
            skipped["code not a current leaf"] += 1
            continue
        if not description:
            skipped["empty description"] += 1
            continue
        country = (data.get("Country") or {}).get("name") or ""
        out.append({
            "id": record["id"], "code": code, "description": description,
            "justification": re.sub(r"\s+", " ", data.get("Justification") or "").strip() or None,
            "country": COUNTRIES.get(country, country[:8] or None), "version_id": record.get("versionId"),
            "snapshot": snapshot,
        })
    return out, skipped


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("snapshot", type=Path, help="Каталог снимка: manifest.json + page_*.json")
    args = p.parse_args()

    from sqlalchemy import create_engine, text

    records, manifest = snapshot_records(args.snapshot)
    snapshot = dt.date.fromisoformat(manifest["on_date"])
    db_url = os.environ.get(
        "DATABASE_URL", "postgresql+psycopg2://feap:feap@localhost:5432/feap",
    ).replace("+asyncpg", "+psycopg2")
    engine = create_engine(db_url)
    with engine.begin() as conn:
        leaves = {code for (code,) in conn.execute(text("SELECT code FROM tnved_tree WHERE is_leaf"))}
        prepared, skipped = rows(records, leaves, snapshot)
        conn.execute(text("""
            INSERT INTO classification_precedents
                (id, source, code, description, justification, country, version_id, snapshot, updated_at)
            VALUES (:id, 'eaeu_1999', :code, :description, :justification, :country, :version_id, :snapshot, now())
            ON CONFLICT (id) DO UPDATE SET
                code = EXCLUDED.code, description = EXCLUDED.description,
                justification = EXCLUDED.justification, country = EXCLUDED.country,
                version_id = EXCLUDED.version_id, snapshot = EXCLUDED.snapshot, updated_at = now()
        """), prepared)
        total = conn.execute(text("SELECT count(*) FROM classification_precedents")).scalar_one()
    engine.dispose()
    print(f"{len(prepared)} of {len(records)} records loaded (snapshot {snapshot}); skipped {skipped}; "
          f"table has {total}")


if __name__ == "__main__":
    main()
