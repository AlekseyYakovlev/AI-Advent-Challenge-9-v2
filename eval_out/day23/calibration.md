# Calibration

- calibration: calibration_set.json sha256 9435112f4e3421a96af029c194177879c89ac7ecadcb0f3fddf6cbc2c31f3407
- control: control_set.json sha256 8c5b76a0590301ab496498b534802e92cbc82072841a188a4b08c19806ceebe2
- candidate_k: 20

## nomic (text-embedding-nomic-embed-text-v1.5)

- answerable gold scores: 0.776, 0.786, 0.788, 0.805, 0.812, 0.853
  - min 0.776 / median 0.797 / max 0.853
- out-of-corpus top-1 scores: 0.749, 0.762, 0.765, 0.769, 0.777, 0.780, 0.787, 0.797
  - min 0.749 / median 0.773 / max 0.797
- answerable without a matching candidate: C02, C04, C06, C08, C10, C12
- threshold: 0.79 (method youden, marker nomic)
- classes are **not separable** by a single cosine cut-off

Control set behaviour at this threshold:

| id | category | top1 | gold | survivors |
|---|---|---|---|---|
| Q01 | direct | 0.776 | 0.738 | 0 |
| Q02 | direct | 0.839 | 0.821 | 14 |
| Q03 | direct | 0.836 | 0.836 | 14 |
| Q04 | direct | 0.756 | 0.736 | 0 |
| Q05 | direct | 0.837 | n/a | 20 |
| Q06 | direct | 0.775 | 0.763 | 0 |
| Q07 | synthesis | 0.801 | n/a | 2 |
| Q08 | synthesis | 0.795 | 0.792 | 2 |
| Q09 | out_of_corpus | 0.756 | n/a | 0 |
| Q10 | out_of_corpus | 0.772 | n/a | 0 |

FTS probe (hybrid on): out-of-corpus questions with an exempt candidate 8/8 (C13, C14, C15, C16, C17, C18, C19, C20); answerable gold rescued only by the exemption: C01, C02, C04, C05, C06, C07, C10, C12

## bge (text-embedding-bge-m3)

- answerable gold scores: 0.674, 0.675, 0.682, 0.686, 0.693, 0.701, 0.716, 0.721, 0.745, 0.762, 0.777, 0.779
  - min 0.674 / median 0.708 / max 0.779
- out-of-corpus top-1 scores: 0.501, 0.513, 0.515, 0.540, 0.552, 0.591, 0.627, 0.656
  - min 0.501 / median 0.546 / max 0.656
- threshold: 0.67 (method midpoint, marker bge-m3)

Control set behaviour at this threshold:

| id | category | top1 | gold | survivors |
|---|---|---|---|---|
| Q01 | direct | 0.639 | 0.639 | 0 |
| Q02 | direct | 0.758 | 0.758 | 1 |
| Q03 | direct | 0.758 | 0.758 | 4 |
| Q04 | direct | 0.698 | 0.698 | 1 |
| Q05 | direct | 0.707 | 0.707 | 4 |
| Q06 | direct | 0.683 | 0.683 | 1 |
| Q07 | synthesis | 0.653 | 0.653 | 0 |
| Q08 | synthesis | 0.634 | 0.634 | 0 |
| Q09 | out_of_corpus | 0.540 | n/a | 0 |
| Q10 | out_of_corpus | 0.517 | n/a | 0 |

FTS probe (hybrid on): out-of-corpus questions with an exempt candidate 8/8 (C13, C14, C15, C16, C17, C18, C19, C20); answerable gold rescued only by the exemption: -
