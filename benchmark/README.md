# FEAP Benchmark

Evaluation suite for the ТН ВЭД ЕАЭС cascading LLM classifier.

## v2 (2026-10): outcomes and money, not only codes

Design and first results: [`docs/benchmark_v2_design.md`](../docs/benchmark_v2_design.md).

| Piece | Where |
|---|---|
| Case schema v2 (tracks, gold tier A–D, verification, sufficiency, review history) | `schema/case_v2.py` |
| benchmark_100 + v32 audit → v2 | `scripts/migrate_v1_to_v2.py` → `data/cases/v2/passport_regression100.jsonl` |
| Outcome per case (✅ correct · 🟡 safe stop · 🔴 silent error · ⚫ failure) | `metrics/outcome.py` |
| Price of a wrong code via the product's payment engine | `metrics/consequences.py` |
| Re-score existing predictions (no LLM) | `scripts/score_v2.py` → `data/results/scored_v2/<run>/` |
| Payment scenarios vs an independent oracle | `scripts/build_calc_scenarios.py`, `scripts/run_calc_scenarios.py` |
| Reference-data fingerprint, budget guard | `evaluator/fingerprint.py`, `evaluator/budget.py` |

```bash
python -m benchmark.scripts.score_v2 \
    --cases benchmark/data/cases/v2/passport_regression100.jsonl \
    --predictions benchmark/data/results/v32_baseline/v32_baseline_predictions.jsonl \
    --run-id v32_baseline --money-date 2026-10-15
python -m benchmark.scripts.run_calc_scenarios --run-id calc_2026-10-16
```

`passport_regression100` is synthetic (tier D) and was used for tuning: use it to compare
versions, not as accuracy on real goods. Paid runs (`evaluate`, `evaluate_audited`) now
estimate their cost first and stop cleanly on a persistent OpenRouter 402.

### Real tier-A sets (phase 1)

| Set | Source | Use |
|---|---|---|
| `eaeu_pd_test400` | EAEU collection of preliminary decisions (registry ЕС 999, nsi.eaeunion.org) | **locked test** |
| `eaeu_pd_test400_short` | same cases, first sentence of the description | locked test: ask vs guess |
| `eaeu_pd_dev150` | same collection, no code shared with test | dev, error analysis |
| `eec_decisions_test` | EEC Collegium decisions since 2022 (gold may stop at the heading) | locked test, hard goods |
| `eec_decisions_dev` | EEC decisions 2017–2021, not re-verified for the 2022 edition | dev |

```bash
python -m benchmark.scripts.collect_eaeu_predecisions --date 2026-10-18   # raw snapshot
python -m benchmark.scripts.build_eaeu_pd_sets --snapshot 2026-10-18     # test/dev sets
python -m benchmark.scripts.build_short_variants --cases benchmark/data/cases/v2/eaeu_pd_test400.jsonl
python -m benchmark.scripts.collect_eec_decisions && python -m benchmark.scripts.build_eec_sets
# run (paid) and score:
python -m benchmark.scripts.evaluate_audited --dataset benchmark/data/cases/v2/eaeu_pd_test400.jsonl \
    --run-id <run> --results-dir benchmark/data/results/<run> --concurrency 1 --timeout 300
python -m benchmark.scripts.score_v2 --cases benchmark/data/cases/v2/eaeu_pd_test400.jsonl \
    --predictions benchmark/data/results/<run>/<run>_predictions.jsonl --run-id <run> --money-date <day>
```

Raw downloads stay in `benchmark/data/raw/` (not in git); the case files are self-contained
and their manifests record the hashes of the snapshots they came from.

The rest of this README describes v1.

## Directory structure

```
benchmark/
├── schema/
│   ├── test_case.py       # TestCase, GroundTruth, TestDataset, Difficulty
│   └── prediction.py      # CasePrediction, CaseResult, save/load helpers
├── collectors/
│   ├── base.py            # Abstract async collector + shared HTTP client
│   ├── fts_decisions.py   # ФТС предварительные решения scraper
│   └── court_decisions.py # КАД Арбитр court decisions scraper
├── metrics/
│   ├── hierarchical.py    # Accuracy at each level (section/group/heading/…)
│   ├── calibration.py     # ECE, MCE, ACE — confidence calibration
│   ├── financial.py       # Duty-rate error and financial impact severity
│   └── report.py          # Aggregate report (print + JSON + txt)
├── evaluator/
│   ├── comparator.py      # (TestCase, CasePrediction) → CaseResult
│   └── runner.py          # Async runner with resume support
├── scripts/
│   ├── scrape_fts.py      # Collect FTS preliminary decisions
│   ├── scrape_kad.py      # Collect KAD Arbitr court cases
│   ├── prepare_dataset.py # Deduplicate, stratify, label difficulty
│   ├── add_manual_cases.py # Hand-crafted edge cases (edit and run)
│   └── evaluate.py        # Full pipeline: classify → compare → report
└── data/
    ├── raw/               # Raw API responses (gitignored)
    ├── cases/             # Processed test datasets (JSONL)
    └── results/           # Evaluation reports and predictions (gitignored)
```

## Quickstart

### Step 1 — Add manual cases (immediate, no scraping needed)

```bash
python -m benchmark.scripts.add_manual_cases \
    --output benchmark/data/cases/manual.jsonl
```

Edit `scripts/add_manual_cases.py` to add more hand-crafted cases.

### Step 2 — Scrape FTS preliminary decisions

> First verify the API endpoint in your browser's DevTools (see note in `collectors/fts_decisions.py`).

```bash
python -m benchmark.scripts.scrape_fts \
    --max 300 \
    --output benchmark/data/cases/fts_raw.jsonl
```

### Step 3 — Scrape court decisions (optional, hard tier)

```bash
python -m benchmark.scripts.scrape_kad \
    --max 100 \
    --output benchmark/data/cases/kad_raw.jsonl
```

**⚠️ Always manually review extracted court cases before use.**

### Step 4 — Prepare the dataset

```bash
python -m benchmark.scripts.prepare_dataset \
    --input benchmark/data/cases/manual.jsonl benchmark/data/cases/fts_raw.jsonl \
    --output benchmark/data/cases/benchmark_v1.jsonl \
    --max 400 --easy 0.30 --medium 0.40 --hard 0.30
```

### Step 5 — Run evaluation

```bash
# Full run (requires running DB + API keys)
python -m benchmark.scripts.evaluate \
    --dataset benchmark/data/cases/benchmark_v1.jsonl \
    --run-id v1

# Quick smoke test (5 cases)
python -m benchmark.scripts.evaluate \
    --dataset benchmark/data/cases/benchmark_v1.jsonl \
    --max 5 --run-id smoke

# Metrics only (from existing predictions)
python -m benchmark.scripts.evaluate \
    --dataset benchmark/data/cases/benchmark_v1.jsonl \
    --predictions benchmark/data/results/v1_predictions.jsonl \
    --metrics-only
```

## Metrics

| Metric | Description | Primary KPI? |
|--------|-------------|:---:|
| `heading_accuracy` | Exact match at 4-digit position level | ★ |
| `subheading_accuracy` | Exact match at 6-digit subheading level | ★ |
| `code_10_accuracy` | Exact match at full 10-digit code | |
| `hierarchical_score` | Weighted composite (see weights below) | |
| `ECE` | Expected Calibration Error (confidence calibration) | |
| `duty_accuracy` | Fraction of cases with correct duty rate | |
| `completion_rate` | Fraction of cases where system completed (vs. stopped) | |

### Hierarchical score weights

| Level | Weight |
|-------|--------|
| Section (I–XXI) | 0.05 |
| Group (2-digit) | 0.10 |
| **Heading (4-digit)** | **0.40** |
| Subheading (6-digit) | 0.25 |
| 8-digit EAEU code | 0.10 |
| 10-digit national code | 0.10 |

Heading carries the most weight because it is the primary classification unit
under the Harmonised System and determines tariff schedule applicability.

## Dataset design

### Stratification

Target distribution: 30% easy / 40% medium / 30% hard.

| Difficulty | Criteria |
|-----------|----------|
| Easy | ОПИ 1 applies directly, code is unambiguous |
| Medium | ОПИ 2–3 needed, 2+ plausible candidates |
| Hard | Court-disputed, composite goods, borderline sections, requires backtracking |

### Ground truth sources (by reliability)

1. ФТС предварительные решения — official, authoritative, ~20k public records
2. КАД Арбитр court decisions — highest authority, hard cases
3. ЕЭК рекомендации по классификации — EAEU-level decisions
4. Expert labelling by a licensed customs broker

### Recommended minimum dataset size

- Smoke test: 10–20 cases
- Development: 100–200 cases
- Production benchmark: 300–500 cases
- Statistical significance for section-level breakdown: ≥ 20 cases per section
