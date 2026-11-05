# Verifier: score_dev148

Cases: 148 scored, 75 wrong codes (W), 73 correct (C); pool `calibration`.

## V1

| | recall(W) | flags(C) | − V0, 95% ДИ | с V0: recall(W) | с V0: flags(C) | тихих ошибок осталось |
|---|---|---|---|---|---|---|
| V0 | 17% (13/75) | 3% (2/73) | | | | |
| P1 | 63% (47/75) | 27% (20/73) | +45% [+31%; +59%] | 69% (52/75) | 29% (21/73) | 23 |
| P2 | 79% (59/75) | 64% (47/73) | +61% [+48%; +73%] | 84% (63/75) | 64% (47/73) | 12 |
| P3 | 85% (64/75) | 70% (51/73) | +68% [+55%; +80%] | 89% (67/75) | 70% (51/73) | 8 |

**H1 не подтверждена** (§8 предрегистрации, плечо V1).

| Тип условия | n | точность | ложные F на пути эталона | ложные T у соседей | U |
|---|---|---|---|---|---|
| semantic | 634 | 82% (522/634) | 6% (15/255) | 11% (40/379) | 9% (57/634) |
| residual | 221 | 71% (158/221) | 14% (23/164) | 4% (2/57) | 17% (38/221) |
| numeric | 29 | 72% (21/29) | 0% (0/13) | 0% (0/16) | 28% (8/29) |
| all | 884 | 79% (701/884) | 9% (38/432) | 9% (42/452) | 12% (103/884) |

| Где ошибся каскад | n | V0 | P1 | P2 | P3 |
|---|---|---|---|---|---|
| group | 30 | 10% (3/30) | 50% (15/30) | 63% (19/30) | 73% (22/30) |
| heading | 19 | 11% (2/19) | 100% (19/19) | 100% (19/19) | 100% (19/19) |
| subheading | 14 | 21% (3/14) | 43% (6/14) | 79% (11/14) | 93% (13/14) |
| eight_digit | 5 | 40% (2/5) | 40% (2/5) | 80% (4/5) | 80% (4/5) |
| national | 7 | 43% (3/7) | 71% (5/7) | 86% (6/7) | 86% (6/7) |

Стоимость $0.0064 на кейс ($0.96 всего), p50 7.9 с, p95 19.9 с, 22.53 вопросов на кейс, сбоев исполнителя 0% (0/3380), ошибок верификатора 0.

## V2

| | recall(W) | flags(C) | − V0, 95% ДИ | с V0: recall(W) | с V0: flags(C) | тихих ошибок осталось |
|---|---|---|---|---|---|---|
| V0 | 17% (13/75) | 3% (2/73) | | | | |
| P1 | 33% (25/75) | 15% (11/73) | +16% [+0%; +31%] | 48% (36/75) | 16% (12/73) | 39 |
| P2 | 51% (38/75) | 25% (18/73) | +33% [+20%; +47%] | 57% (43/75) | 25% (18/73) | 32 |
| P3 | 83% (62/75) | 38% (28/73) | +65% [+53%; +76%] | 84% (63/75) | 38% (28/73) | 12 |

| Тип условия | n | точность | ложные F на пути эталона | ложные T у соседей | U |
|---|---|---|---|---|---|
| semantic | 634 | 84% (530/634) | 8% (21/255) | 4% (15/379) | 11% (68/634) |
| residual | 221 | 74% (163/221) | 9% (15/164) | 5% (3/57) | 18% (40/221) |
| numeric | 29 | 72% (21/29) | 0% (0/13) | 0% (0/16) | 28% (8/29) |
| all | 884 | 81% (714/884) | 8% (36/432) | 4% (18/452) | 13% (116/884) |

| Где ошибся каскад | n | V0 | P1 | P2 | P3 |
|---|---|---|---|---|---|
| group | 30 | 10% (3/30) | 27% (8/30) | 43% (13/30) | 70% (21/30) |
| heading | 19 | 11% (2/19) | 42% (8/19) | 53% (10/19) | 95% (18/19) |
| subheading | 14 | 21% (3/14) | 21% (3/14) | 43% (6/14) | 86% (12/14) |
| eight_digit | 5 | 40% (2/5) | 60% (3/5) | 80% (4/5) | 80% (4/5) |
| national | 7 | 43% (3/7) | 43% (3/7) | 71% (5/7) | 100% (7/7) |

Стоимость $0.0030 на кейс ($0.45 всего), p50 2.1 с, p95 5.2 с, 22.53 вопросов на кейс, сбоев исполнителя 0% (0/3380), ошибок верификатора 0.

## Неверные коды

| Кейс | эталон | каскад | уровень | V0 | V1 | V2 |
|---|---|---|---|---|---|---|
| PD-BY-0c29a819 | 0406900100 | 0406105009 | subheading |  | ·23 S4:medium S5:weak | ·23 S4:medium S5:weak |
| PD-BY-0ee433a8 | 0406909200 | 0406908200 | eight_digit |  | ·23 S5:weak S6:medium | 123 S1:strong S5:weak |
| PD-BY-140c752b | 7216611000 | 7308909809 | group |  | 123 S2:strong S3:strong | 123 S2:strong S3:strong |
| PD-BY-22682727 | 4811490000 | 4810390000 | heading | ⚑ | 123 S1:strong S3:strong S5:weak | ··3 S5:weak |
| PD-BY-28408573 | 9021290004 | 9021290009 | national |  | 123 S2:strong S6:medium | 123 S2:strong |
| PD-BY-2ae9799b | 4411149000 | 4421991000 | heading |  | 123 S2:strong S3:strong | ··3 S5:weak |
| PD-BY-33febc59 | 4819200000 | 4823908597 | heading |  | 123 S2:strong S3:strong | 123 S2:strong S3:strong |
| PD-BY-4445b404 | 6116930000 | 9506999000 | group |  | ··· | ··· |
| PD-BY-48c28f26 | 9603909100 | 3924900009 | group |  | ··· | ··· |
| PD-BY-55273f30 | 8426410003 | 8427201909 | heading |  | 123 S2:strong S3:strong S6:medium | ·23 S5:weak S6:medium |
| PD-BY-80f5ab18 | 1704906500 | 1704909800 | eight_digit |  | 123 S2:strong S5:weak | 123 S2:strong S3:strong |
| PD-BY-853aafd2 | 0408998000 | 2106909300 | group | ⚑ | ··· | ·23 S4:medium |
| PD-BY-95751e68 | 7116208000 | 6802999000 | group |  | 123 S2:strong | 123 S2:strong S3:strong |
| PD-BY-957cc89d | 7013499900 | 7010909101 | heading |  | 123 S2:strong S3:strong S4:medium S5:weak | 123 S2:strong S4:medium |
| PD-BY-ae3f8ecc | 2621900000 | 3824999608 | group |  | 123 S2:strong S5:weak | 123 S2:strong S3:strong S5:weak |
| PD-BY-b0dc3475 | 4818300000 | 4818901000 | subheading |  | 123 S2:strong S3:strong S6:strong | ··· |
| PD-BY-b668d056 | 9603909900 | 9018908409 | group |  | ·23 S5:weak S6:medium | ··3 S5:weak |
| PD-BY-b84af756 | 1102907000 | 1901200000 | group |  | ··· | ··· |
| PD-BY-c63e6e79 | 4401390000 | 4602199000 | group |  | 123 S1:strong | 123 S1:strong |
| PD-BY-c85420d0 | 9018390000 | 9018311009 | subheading |  | ·23 S5:weak S6:medium | ··3 S5:weak |
| PD-BY-caaee662 | 0406105009 | 0406105002 | national |  | 123 S1:strong S5:weak | ··3 S5:weak |
| PD-BY-cb4051d7 | 3909310000 | 3824100000 | group |  | ··3 S5:weak | ··3 S5:weak |
| PD-BY-dd32e910 | 2309909609 | 3824999608 | group |  | 123 S2:strong S5:weak | ··3 S5:weak |
| PD-BY-dd5f06f6 | 6810990000 | 6815990008 | heading |  | 123 S6:strong | ··3 S5:weak |
| PD-BY-f516776e | 7326199009 | 9401990009 | group |  | ··· | ·23 S6:medium |
| PD-BY-f9d85ae3 | 7315119000 | 8431310000 | group |  | ·23 S6:medium | ··· |
| PD-KZ-07a05db1 | 8544499509 | 8544499101 | eight_digit | ⚑ | ·23 S4:medium S5:weak | ·23 S4:medium S5:weak |
| PD-KZ-145026ed | 8436809000 | 8479899707 | heading |  | 123 S3:strong S5:weak | 123 S3:strong S5:weak |
| PD-KZ-14b2dfed | 8709199000 | 8479899707 | group |  | 123 S2:strong S5:weak | 123 S2:strong S5:weak |
| PD-KZ-1ba23c86 | 8517699000 | 8517790009 | subheading |  | 123 S1:strong S5:weak | 123 S1:strong S5:weak |
| PD-KZ-212ba0e5 | 8705908001 | 8705908005 | national |  | 123 S2:strong S3:strong | 123 S2:strong |
| PD-KZ-2163f4d5 | 9506290000 | 9506919000 | subheading | ⚑ | 123 S2:strong S3:strong S5:weak | 123 S2:strong S3:strong S5:weak |
| PD-KZ-4447b9b6 | 8702101994 | 8702101997 | national | ⚑ | 123 S2:strong S4:medium S6:strong | 123 S2:strong S4:medium S5:weak |
| PD-KZ-4972fc20 | 2612209000 | 2612109000 | subheading | ⚑ | ··3 S5:weak | ··3 S5:weak |
| PD-KZ-4e1f1a01 | 7019190000 | 7019150000 | subheading |  | ·23 S6:medium | ·23 S6:medium |
| PD-KZ-52d51e97 | 8412908009 | 8412904008 | eight_digit |  | 123 S1:strong S3:strong | 123 S1:strong |
| PD-KZ-5aff5f6f | 4415202000 | 4421999000 | heading |  | 123 S2:strong S3:strong | 123 S2:strong S3:strong |
| PD-KZ-63d8c3ca | 4408909509 | 4408901509 | eight_digit | ⚑ | ··· | ··· |
| PD-KZ-66eb3b1f | 4421999000 | 4412330000 | heading | ⚑ | 123 S4:medium S6:strong | ·23 S4:medium |
| PD-KZ-68d6f053 | 9031908500 | 8479907000 | group | ⚑ | 123 S5:weak S6:strong | ·23 S4:medium S5:weak |
| PD-KZ-6a48ef76 | 7106920000 | 7115900000 | heading |  | 123 S2:strong S3:strong | 123 S2:strong S3:strong |
| PD-KZ-7b59ffda | 3006910000 | 9018908409 | group |  | 123 S3:strong S5:weak | ··3 S5:weak |
| PD-KZ-7c120345 | 6810910000 | 6811820000 | heading |  | 123 S3:strong S5:weak | ··· |
| PD-KZ-864d7810 | 5407940000 | 5407740000 | subheading |  | ·23 S5:weak S6:medium | ··3 S5:weak |
| PD-KZ-a5592608 | 4404100001 | 4404100002 | national | ⚑ | 123 S3:strong S5:weak S6:strong | ··3 S5:weak |
| PD-KZ-a8e870aa | 8708709909 | 4011209000 | group | ⚑ | 123 S1:strong S4:medium | ·23 S4:medium |
| PD-KZ-aa751ae1 | 8607211009 | 8607290000 | subheading |  | 123 S2:strong S3:strong S6:medium | ··3 S5:weak |
| PD-KZ-ad62ba2a | 8414108900 | 8414809000 | subheading |  | ·23 S3:medium | 123 S1:strong |
| PD-KZ-b5c547aa | 6307909800 | 3926909709 | group |  | 123 S2:strong S5:weak | ··3 S5:weak |
| PD-KZ-bbe961e6 | 2709009009 | 2709009003 | national |  | ·23 S4:medium S5:weak S6:medium | ·23 S5:weak S6:medium |
| PD-KZ-cb3c3ced | 1511909902 | 1517909100 | heading |  | 123 S1:strong S3:strong S5:weak | 123 S1:strong S5:weak |
| PD-KZ-daaea0a4 | 6006329000 | 6001920000 | heading |  | 123 S2:strong | ··3 S5:weak |
| PD-KZ-e4ffdf54 | 9031803800 | 8479820000 | group |  | 123 S2:strong S3:strong | ··3 S5:weak |
| PD-KZ-e5de8355 | 9030100000 | 9027890000 | heading |  | 123 S2:strong S3:strong S5:weak | ··3 S5:weak |
| PD-KZ-ee63fc53 | 5703390009 | 5703310009 | subheading |  | ··3 S5:weak | ··3 S5:weak |
| PD-KZ-f3f0c02e | 2620999509 | 2620300000 | subheading |  | 123 S1:strong S3:strong | ·23 S4:medium |
| PD-KZ-faec5d7c | 3814009000 | 2922170000 | group |  | ·23 S3:medium | ·23 S3:medium |
| PD-KZ-fff9eca2 | 7112910000 | 7112990000 | subheading | ⚑ | 123 S5:weak S6:strong | ··3 S5:weak |
| PD-RU-003f12b2 | 4418790000 | 4412330000 | heading |  | 123 S1:strong S3:strong | 123 S1:strong S3:strong S4:medium |
| PD-RU-252fcc0d | 3707909000 | 8443999000 | group |  | 123 S2:strong S5:weak | 123 S2:strong S5:weak |
| PD-RU-2bc0f216 | 8302500000 | 4420909900 | group |  | 123 S1:strong S3:strong S5:weak | 123 S1:strong |
| PD-RU-2ddb7155 | 9107000000 | 8536490000 | group |  | ··· | ··· |
| PD-RU-37715b0b | 4901100000 | 4911990000 | heading |  | 123 S2:strong S3:strong | ··3 S5:weak |
| PD-RU-4b2da111 | 6815990008 | 6802931000 | heading |  | 123 S2:strong S3:strong | 123 S2:strong S3:strong |
| PD-RU-50cd1a6f | 1516109000 | 3824999208 | group |  | ··3 S5:weak | ··3 S5:weak |
| PD-RU-53aa17ec | 1604209090 | 1604199780 | subheading |  | ··· | ··· |
| PD-RU-6e74d842 | 6804210000 | 8202998000 | group |  | ·23 S3:medium | ··· |
| PD-RU-87c44433 | 9506911000 | 9019109009 | group |  | ··· | ··· |
| PD-RU-9b1d6c04 | 1212999509 | 1106100000 | group |  | ··· | ··· |
| PD-RU-a0cc09c0 | 8205400000 | 3926909709 | group |  | ··3 S5:weak | ··3 S5:weak |
| PD-RU-a988a0c8 | 3917320001 | 4009110000 | group |  | 123 S3:strong S4:medium | ··· |
| PD-RU-c9df588b | 0902100009 | 0902100001 | national | ⚑ | ··· | ·23 S4:medium |
| PD-RU-d1e50e4a | 8479810000 | 8477200000 | heading |  | 123 S3:strong S5:weak | ··3 S5:weak |
| PD-RU-d6ac15c0 | 4811590009 | 4810923000 | heading |  | 123 S2:strong S3:strong | ··3 S5:weak |
| PD-RU-f1b1bd81 | 9032890000 | 8537109100 | group |  | 123 S1:strong S2:strong S3:strong | 123 S2:strong S3:strong |
