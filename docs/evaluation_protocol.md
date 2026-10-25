# Evaluation protocol

1. **Fix** the dataset revision, seed(s), method list and retrieval config; they are saved in `config.json` with the results.
2. **Split** (optional): seeded task-key split; tune weights on train only; report test-split numbers.
3. **Run** each method on each task (`repeats` ≥ 1). Systems receive `SystemInput` (repo snapshot, report text, optionally failing tests) and never labels.
4. **Score** with labels: top-1, top-5, MRR, recall@1/3/5, context recall, irrelevant files in context, context chars, latency.
5. **Aggregate:** mean with 95% seeded bootstrap CI over tasks.
6. **Compare** each method with the reference by paired difference per task; two-sided sign-flip permutation test (exact when ≤ 14 non-zero pairs, seeded Monte-Carlo otherwise).
7. **Interpret** with the built-in rule: fewer than `MIN_N_FOR_INFERENCE = 20` tasks are always "inconclusive"; otherwise "improvement", "regression" or "no detectable difference" by CI and p-value. Report effect sizes, not only p-values.
8. **Repair stage** (optional, needs a real model): `resolved` = baseline reproduced AND targeted tests pass AND broad suite passes. Without a provider it is reported as skipped.
9. **Archive** `results.csv/json`, `summary.csv/json`, `paired_comparisons.csv`, `comparison.md`, `localization.png`, `config.json`.

Reporting template:

| method | n | top-1 [CI] | top-5 [CI] | MRR [CI] | ctx chars | latency ms | vs reference (mean diff, CI, p) | reading |
|---|---|---|---|---|---|---|---|---|
| _fill from summary.csv_ | | | | | | | | |
