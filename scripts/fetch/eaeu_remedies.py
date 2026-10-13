"""Реестр мер защиты внутреннего рынка ЕАЭС → data/trade_remedies_seed.json.

Источник: открытый реестр ЕЭК remedies.eaeunion.org (портал Департамента
защиты внутреннего рынка). Данные отдаются SPA-порталу через
POST {api}/find?collection=zvr.v_actionsregistry с телом "{}" — это
публичный эндпоинт без авторизации, тот же, что использует сайт.

Ставки разбираются из текста «формы меры» (feap.tariff.remedies.record_to_seed);
меры, текст которых не удалось разобрать, помечаются needs_review=true
(в расчёте ДТ такие меры требуют ручного ввода ставки).

Usage:
    python scripts/fetch/eaeu_remedies.py                      # живой реестр
    python scripts/fetch/eaeu_remedies.py --from-file dump.json
    python scripts/fetch/eaeu_remedies.py --include-expired

Затем: python scripts/seed/trade_remedies.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from feap.tariff.remedies import record_to_seed  # noqa: E402

API_URL = "https://remedies.eaeunion.org/spd2/find?collection=zvr.v_actionsregistry"
DEFAULT_OUT = PROJECT_ROOT / "data" / "trade_remedies_seed.json"


def fetch_registry(url: str = API_URL) -> list[dict]:
    resp = httpx.post(
        url, content="{}",
        headers={
            "Content-Type": "text/plain",
            "Origin": "https://remedies.eaeunion.org",
            "Referer": "https://remedies.eaeunion.org/dimd/ru/security/common",
        },
        timeout=60,
    )
    resp.raise_for_status()
    return list(resp.json().get("result") or [])


def build_seed(records: list[dict], *, today: dt.date, include_expired: bool) -> dict:
    measures = [record_to_seed(r, today=today) for r in records]
    if not include_expired:
        measures = [m for m in measures if m["status"] != "expired"]
    measures.sort(key=lambda m: (m["kind"], int("".join(c for c in m["measure_no"] if c.isdigit()) or 0)))
    return {
        "_source": API_URL,
        "_fetched_at": today.isoformat(),
        "_registry_total": len(records),
        "_comment": (
            "Снимок реестра мер ЕЭК. Ставки разобраны из текста формы меры; "
            "needs_review=true — ставки требуют ручного ввода. Не править руками: "
            "перегенерировать scripts/fetch/eaeu_remedies.py."
        ),
        "measures": measures,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--from-file", type=Path, help="JSON-ответ реестра вместо живого запроса")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--include-expired", action="store_true")
    args = parser.parse_args()

    if args.from_file:
        records = json.loads(args.from_file.read_text(encoding="utf-8")).get("result") or []
    else:
        print(f"Fetching EAEU remedies registry: {API_URL}")
        records = fetch_registry()
    if not records:
        print("ERROR: реестр пуст", file=sys.stderr)
        sys.exit(1)

    seed = build_seed(records, today=dt.date.today(), include_expired=args.include_expired)
    args.out.write_text(json.dumps(seed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    review = sum(1 for m in seed["measures"] if m["needs_review"])
    print(f"OK: {len(seed['measures'])} мер (из {len(records)} в реестре), "
          f"needs_review={review} → {args.out}")


if __name__ == "__main__":
    main()
