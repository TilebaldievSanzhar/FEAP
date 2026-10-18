"""Download the EAEU collection of preliminary classification decisions (ЕС 999).

Run:
    python -m benchmark.scripts.collect_eaeu_predecisions --date 2026-10-18
Output: benchmark/data/raw/eaeu_predecisions/<date>/page_*.json + manifest.json
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
from pathlib import Path

from benchmark.collectors.eaeu_predecisions import collect


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)-8s %(message)s')
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--date', type=dt.date.fromisoformat, default=dt.date.today(),
                   help='Registry state on this date')
    p.add_argument('--out-root', type=Path, default=Path('benchmark/data/raw/eaeu_predecisions'))
    p.add_argument('--page-size', type=int, default=1000)
    args = p.parse_args()
    manifest = asyncio.run(collect(args.out_root / args.date.isoformat(),
                                   on_date=args.date, page_size=args.page_size))
    print(f"{manifest['records']} of {manifest['total_count']} records → "
          f"{args.out_root / args.date.isoformat()}")


if __name__ == '__main__':
    main()
