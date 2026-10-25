# synthetic baseline

Tasks: 6  |  reference method: `hybrid_conventional`  |  seed: 0

> **Warning:** n=6 tasks is below 20; all comparisons are descriptive, not evidence of improvement.

| method | top-1 | top-5 | MRR | recall@5 | context chars | irrelevant files | median latency ms |
|---|---|---|---|---|---|---|---|
| lexical | 0.667 [0.333, 1.000] | 0.833 [0.500, 1.000] | 0.750 [0.417, 1.000] | 0.833 [0.500, 1.000] | 3224 | 6.50 | 1.3 |
| semantic | 0.500 [0.167, 0.833] | 0.667 [0.333, 1.000] | 0.583 [0.250, 0.889] | 0.667 [0.333, 1.000] | 3952 | 8.50 | 1.0 |
| hybrid_conventional | 0.667 [0.333, 1.000] | 0.833 [0.500, 1.000] | 0.750 [0.472, 1.000] | 0.833 [0.500, 1.000] | 4270 | 8.50 | 2.0 |
| dependency_aware_hybrid | 0.500 [0.167, 0.833] | 1.000 [1.000, 1.000] | 0.722 [0.500, 0.917] | 1.000 [1.000, 1.000] | 4415 | 8.33 | 4.3 |
| adaptive | 0.500 [0.167, 0.833] | 1.000 [1.000, 1.000] | 0.722 [0.500, 0.917] | 1.000 [1.000, 1.000] | 4415 | 8.33 | 4.2 |
| dependency_aware_hybrid-minus-lexical | 0.500 [0.167, 0.833] | 0.833 [0.500, 1.000] | 0.653 [0.375, 0.917] | 0.833 [0.500, 1.000] | 4305 | 8.50 | 2.8 |
| dependency_aware_hybrid-minus-symbol_exact | 0.500 [0.167, 0.833] | 1.000 [1.000, 1.000] | 0.722 [0.500, 0.917] | 1.000 [1.000, 1.000] | 4408 | 8.50 | 3.9 |
| dependency_aware_hybrid-minus-semantic | 0.667 [0.333, 1.000] | 1.000 [1.000, 1.000] | 0.833 [0.667, 1.000] | 1.000 [1.000, 1.000] | 4407 | 7.83 | 3.0 |
| dependency_aware_hybrid-minus-graph | 0.500 [0.167, 0.833] | 1.000 [1.000, 1.000] | 0.694 [0.472, 0.917] | 1.000 [1.000, 1.000] | 4288 | 8.50 | 2.3 |
| dependency_aware_hybrid-minus-test | 0.500 [0.167, 0.833] | 1.000 [1.000, 1.000] | 0.694 [0.472, 0.917] | 1.000 [1.000, 1.000] | 4393 | 8.50 | 4.3 |
| dependency_aware_hybrid-minus-trace | 0.500 [0.167, 0.833] | 1.000 [1.000, 1.000] | 0.722 [0.500, 0.917] | 1.000 [1.000, 1.000] | 4421 | 8.50 | 3.9 |

## Paired comparison against reference (MRR)

| method | mean diff | 95% CI | p | W/L/T | reading |
|---|---|---|---|---|---|
| lexical | +0.000 | [-0.083, +0.083] | 1.000 | 1/1/4 | inconclusive: only n=6 tasks (< 20); descriptive only |
| semantic | -0.167 | [-0.389, +0.000] | 0.500 | 0/2/4 | inconclusive: only n=6 tasks (< 20); descriptive only |
| dependency_aware_hybrid | -0.028 | [-0.250, +0.167] | 1.000 | 1/1/4 | inconclusive: only n=6 tasks (< 20); descriptive only |
| adaptive | -0.028 | [-0.250, +0.167] | 1.000 | 1/1/4 | inconclusive: only n=6 tasks (< 20); descriptive only |
| dependency_aware_hybrid-minus-lexical | -0.097 | [-0.375, +0.139] | 0.750 | 1/2/3 | inconclusive: only n=6 tasks (< 20); descriptive only |
| dependency_aware_hybrid-minus-symbol_exact | -0.028 | [-0.250, +0.167] | 1.000 | 1/1/4 | inconclusive: only n=6 tasks (< 20); descriptive only |
| dependency_aware_hybrid-minus-semantic | +0.083 | [-0.222, +0.417] | 0.750 | 2/1/3 | inconclusive: only n=6 tasks (< 20); descriptive only |
| dependency_aware_hybrid-minus-graph | -0.056 | [-0.250, +0.083] | 1.000 | 1/1/4 | inconclusive: only n=6 tasks (< 20); descriptive only |
| dependency_aware_hybrid-minus-test | -0.056 | [-0.250, +0.083] | 1.000 | 1/1/4 | inconclusive: only n=6 tasks (< 20); descriptive only |
| dependency_aware_hybrid-minus-trace | -0.028 | [-0.250, +0.167] | 1.000 | 1/1/4 | inconclusive: only n=6 tasks (< 20); descriptive only |
