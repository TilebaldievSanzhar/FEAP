"""Paired L4/L5 prompt/model experiment on captured contexts and choice schemas.

Ground truth is used only after calls, never to construct either model request.
The last context per case is used; parent-mismatched cases are reported separately.
"""
from __future__ import annotations

import argparse
import asyncio
import contextvars
import hashlib
import json
import random
from pathlib import Path

from benchmark.schema.test_case import TestDataset
from feap.cascade.selection import classify_choice
from feap.llm.client import classify, get_provider
from feap.prompts.registry import get_env
from feap.schemas.subposition import SubpositionResult
from feap.schemas.subsubposition import SubsubpositionResult

_calls = contextvars.ContextVar('probe_calls')


async def run(args):
    output_model = SubpositionResult if args.level == 4 else SubsubpositionResult
    decision_field = 'selected_subposition' if args.level == 4 else 'selected_code_10'
    parent_length = 4 if args.level == 4 else 6
    cases = {c.id: c for c in TestDataset.load_jsonl(args.dataset)}
    contexts = {}
    branch_shortlists = {}
    for line in args.trace.read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        schema = (row.get('response_format') or {}).get('json_schema', {})
        if schema.get('name') == 'SubsubpositionShortlistResult':
            try:
                branch_shortlists[row['case_id']] = json.loads(row['raw_content'])
            except (ValueError, TypeError):
                branch_shortlists.pop(row['case_id'], None)
        if schema.get('name') == output_model.__name__ and row['case_id'] in cases:
            enum = schema['schema']['properties'][decision_field].get('enum')
            if enum:
                if args.branch_context:
                    row['branch_shortlist'] = branch_shortlists.get(row['case_id'])
                    if not row['branch_shortlist']:
                        continue
                contexts[row['case_id']] = row
    if not contexts:
        raise ValueError('No matching contexts with explicit choice enums in trace')
    variant_text = args.variant.read_text(encoding='utf-8')
    variant = get_env().from_string(variant_text)
    branch_text = args.branch_context.read_text(encoding='utf-8') if args.branch_context else None
    branch_template = get_env().from_string(branch_text) if branch_text else None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise FileExistsError('Use a new output path for each experiment')
    args.output.with_suffix('.manifest.json').write_text(json.dumps({
        'trace_sha256': hashlib.sha256(args.trace.read_bytes()).hexdigest(),
        'dataset_sha256': hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        'variant_sha256': hashlib.sha256(args.variant.read_bytes()).hexdigest(),
        'level': args.level,
        'branch_context_sha256': hashlib.sha256(branch_text.encode()).hexdigest()
        if branch_text else None,
        'models': {'control': args.model, 'variant': args.variant_model or args.model},
        'repeat': args.repeat, 'seed': args.seed,
        'concurrency': args.concurrency,
        'case_ids': sorted(contexts),
    }, indent=2), encoding='utf-8')

    provider = get_provider()
    original_api = provider._call_api

    async def capture(client, **kwargs):
        response = await original_api(client, **kwargs)
        _calls.get().append({
            'raw_content': response.choices[0].message.content,
            'usage': response.usage.model_dump() if response.usage else None,
        })
        return response

    provider._call_api = capture
    semaphore = asyncio.Semaphore(args.concurrency)
    randomizer = random.Random(args.seed)
    tasks = []

    async def pair(cid, row, repeat, order):
        async with semaphore:
            enum = row['response_format']['json_schema']['schema']['properties'][
                decision_field]['enum']
            choices = {c for c in enum if c}
            parents = {c[:parent_length] for c in choices}
            if len(parents) != 1:
                raise ValueError('Context contains more than one parent code')
            parent = parents.pop()
            messages = {m['role']: m['content'] for m in row['messages']}
            for name in order:
                model = (args.variant_model or args.model) if name == 'variant' else args.model
                user_message = messages['user']
                if name == 'variant' and branch_template:
                    user_message += '\n\n' + branch_template.render(
                        shortlist=row['branch_shortlist']).strip()
                recorded_calls = []
                token = _calls.set(recorded_calls)
                result = None
                error = None
                try:
                    result = await classify_choice(
                        classify_fn=classify, choices={decision_field: choices},
                        level=args.level, output_model=output_model,
                        model_override=model,
                        system_prompt=(messages['system'] if name == 'control' else
                                       variant.render(position_code=parent,
                                                      subposition_code=parent).strip()),
                        user_message=user_message,
                    )
                except Exception as exc:
                    error = f'{type(exc).__name__}: {exc}'
                finally:
                    _calls.reset(token)
                selected = getattr(result, decision_field) if result else None
                ground_truth = cases[cid].ground_truth
                expected = ground_truth.subheading if args.level == 4 else ground_truth.code_10
                entry = {
                    'case_id': cid, 'repeat': repeat, 'variant': name,
                    'model': model,
                    'parent_correct': parent == ground_truth.code_10[:parent_length],
                    'selected': selected, 'expected': expected,
                    'correct': selected == expected,
                    'result': result.model_dump() if result else None,
                    'error': error, 'calls': recorded_calls,
                }
                with args.output.open('a', encoding='utf-8') as out:
                    out.write(json.dumps(entry, ensure_ascii=False) + '\n')
                print(cid, repeat, name, selected, entry['correct'], flush=True)

    try:
        for cid, row in sorted(contexts.items()):
            for repeat in range(args.repeat):
                order = ['control', 'variant']
                randomizer.shuffle(order)
                tasks.append(pair(cid, row, repeat, order))
        await asyncio.gather(*tasks)
    finally:
        provider._call_api = original_api


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--variant', type=Path, required=True)
    parser.add_argument('--level', type=int, choices=(4, 5), default=4)
    parser.add_argument('--branch-context', type=Path,
                        help='L5 only: append a template rendered with the preceding L5a shortlist')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', default='google/gemini-3.5-flash-lite')
    parser.add_argument('--variant-model', help='Defaults to the control model')
    parser.add_argument('--repeat', type=int, default=2)
    parser.add_argument('--seed', type=int, default=3101)
    parser.add_argument('--concurrency', type=int, default=2)
    args = parser.parse_args()
    if args.repeat < 1 or args.concurrency < 1:
        parser.error('--repeat and --concurrency must be positive')
    if args.branch_context and args.level != 5:
        parser.error('--branch-context requires --level 5')
    asyncio.run(run(args))


if __name__ == '__main__':
    main()
