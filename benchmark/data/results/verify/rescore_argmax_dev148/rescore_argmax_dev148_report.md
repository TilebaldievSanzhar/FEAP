# Verifier answers under other thresholds: rescore_argmax_dev148

Cases: 148 scored, pool `calibration`. Post-hoc reading of cached answers; the pre-registered decision is unchanged. The frozen thresholds reproduce the stored statuses exactly.

| Arm | Thresholds | atomic accuracy | U | false F on gold path | false T on siblings | precision of T/F | P1 recall(W) / flags(C) | P2 recall(W) / flags(C) |
|---|---|---|---|---|---|---|---|---|
| V1 | stage 1: T ≥ 0.5, F ≥ 0.8 | 79% | 12% | 9% | 9% | 90% | 63% / 27% | 79% / 64% |
| V2 | stage 1: T ≥ 0.5, F ≥ 0.8 | 81% | 13% | 8% | 4% | 93% | 33% / 15% | 51% / 25% |
| V3 jev-1.14 | stage 1: T ≥ 0.5, F ≥ 0.8 | 65% | 29% | 6% | 7% | 91% | 52% / 26% | 81% / 63% |
| V3 d1.1 | stage 1: T ≥ 0.5, F ≥ 0.8 | 51% | 45% | 4% | 5% | 92% | 31% / 21% | 75% / 58% |
| V3 jev-1.14 | argmax | 79% | 12% | 10% | 9% | 90% | 65% / 30% | 79% / 60% |
| V3 d1.1 | argmax | 74% | 18% | 8% | 8% | 90% | 63% / 26% | 80% / 56% |
