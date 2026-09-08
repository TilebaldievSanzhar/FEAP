"""Compare two complete prediction files on exactly the same dataset."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from benchmark.metrics.paired import compare
from benchmark.schema.prediction import load_predictions
from benchmark.schema.test_case import TestDataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = compare(TestDataset.load_jsonl(args.dataset),
                     load_predictions(args.baseline), load_predictions(args.candidate))
    report['inputs'] = {
        name: {'path': path.as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        for name, path in (('dataset', args.dataset), ('baseline', args.baseline),
                           ('candidate', args.candidate))
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report['paired_code_10'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
