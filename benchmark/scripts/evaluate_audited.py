"""Run the benchmark with per-call raw responses and reproducibility metadata.

Usage: python -m benchmark.scripts.evaluate_audited <evaluate.py arguments>
Trace files contain benchmark descriptions and model responses; no credentials.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from benchmark.evaluator.runner import BenchmarkRunner
from benchmark.scripts import evaluate
from feap.llm.client import get_provider
from feap.llm.models import resolve_model

case_id = contextvars.ContextVar('benchmark_case_id', default=None)


def main():
    args = evaluate.parse_args()
    args.results_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.results_dir / args.run_id
    manifest = {
        'started_at': datetime.now(UTC).isoformat(),
        'git_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'git_diff_sha256': hashlib.sha256(subprocess.check_output(['git', 'diff', 'HEAD'])).hexdigest(),
        'dataset_sha256': hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        'models': {str(level): resolve_model(level) for level in range(6)},
        'concurrency': args.concurrency,
        'timeout': args.timeout,
        'source_sha256': {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(Path('src/feap').rglob('*'))
            if path.suffix in {'.py', '.jinja2'}
        },
    }
    manifest_path = prefix.with_name(prefix.name + '_manifest.json')
    if manifest_path.exists() and not args.no_resume:
        previous = json.loads(manifest_path.read_text(encoding='utf-8'))
        for key in ('dataset_sha256', 'models', 'source_sha256'):
            if previous.get(key) != manifest[key]:
                raise ValueError(f'Cannot resume with changed {key}; use a new run-id')
        manifest['previous_invocations'] = [
            *previous.get('previous_invocations', []),
            {key: previous[key] for key in ('started_at', 'concurrency', 'timeout')},
        ]
    manifest_path.write_text(
        json.dumps(manifest, indent=2), encoding='utf-8',
    )
    original_case = BenchmarkRunner._classify_case
    original_api = get_provider()._call_api
    trace_path = prefix.with_name(prefix.name + '_trace.jsonl')
    if args.no_resume:
        trace_path.write_text('', encoding='utf-8')

    async def run_case(self, case):
        token = case_id.set(case.id)
        try:
            return await original_case(self, case)
        finally:
            case_id.reset(token)

    async def capture(client, **kwargs):
        response = await original_api(client, **kwargs)
        record = {
            'recorded_at': datetime.now(UTC).isoformat(),
            'case_id': case_id.get(),
            'model': kwargs['model'],
            'messages': kwargs['messages'],
            'response_format': kwargs.get('response_format'),
            'raw_content': response.choices[0].message.content,
            'finish_reason': response.choices[0].finish_reason,
            'usage': response.usage.model_dump() if response.usage else None,
        }
        with trace_path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
        return response

    BenchmarkRunner._classify_case = run_case
    get_provider()._call_api = capture
    try:
        evaluate.main()
    finally:
        BenchmarkRunner._classify_case = original_case
        get_provider()._call_api = original_api


if __name__ == '__main__':
    main()
