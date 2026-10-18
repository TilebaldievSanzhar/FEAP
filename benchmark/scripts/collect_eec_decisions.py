"""Download EEC Collegium classification decisions and parse their items.

Run:
    python -m benchmark.scripts.collect_eec_decisions
Output: benchmark/data/raw/eec_decisions/ (list.html, <date>_<number>.docx|pdf, decisions.json)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from collections import Counter
from pathlib import Path

from benchmark.collectors.eec_decisions import collect


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)-8s %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--out-dir', type=Path, default=Path('benchmark/data/raw/eec_decisions'))
    args = p.parse_args()
    decisions = asyncio.run(collect(args.out_dir))
    items = sum(len(d['items']) for d in decisions)
    print(f"{len(decisions)} decisions, {items} items; errors: "
          f"{dict(Counter(d['error'] for d in decisions if d['error']))}")


if __name__ == '__main__':
    main()
