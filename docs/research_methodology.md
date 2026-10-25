# Research methodology

## Questions
- **RQ1** Does adding dependency-graph signals to a conventional hybrid (lexical + semantic) retriever change bug-localization accuracy (top-1, top-5, MRR, recall@k)?
- **RQ2** Which signals contribute (ablation: remove one at a time)?
- **RQ3** What does any gain cost in latency and context size?
- **RQ4** (needs a real model) Does test verification change what "fixed" means compared with trusting generated patches?

Any outcome is acceptable and must be reported as found: improvement, no difference, or regression.

## Hypotheses (stated before looking at results)
H1: dependency-aware hybrid ≥ conventional hybrid on top-5 and recall@5. H0: no difference. Top-1 may go either way.

## Systems compared
`lexical`, `semantic`, `hybrid_conventional` (reference), `dependency_aware_hybrid`, `adaptive`, plus automatic single-signal ablations of `dependency_aware_hybrid`. All use the same chunks, index and context budget.

## Controls
Fixed seeds; deterministic offline embedder; identical index per task; label-free `SystemInput`; train/test split with weight tuning on the train split only; failing-test names withheld unless `provide_failing_tests` is set.

## What exists so far
One run on the synthetic suite (n = 6): `results/synthetic_baseline/`. Observed (descriptive only): dependency-aware hybrid reached top-5 = 1.00 vs 0.83 for the conventional hybrid, but top-1 = 0.50 vs 0.67 and MRR 0.722 vs 0.750. All paired CIs include 0 and n < 20, so the framework labels every comparison **inconclusive**. This does not support H1.

## Threats to validity
Tiny synthetic set authored by the same people who built the retriever; bugs are injected and may favor name-based signals; hashing embedder is not neural; no real-world repositories evaluated; multiple comparisons not corrected; `resolved` approximates SWE-bench semantics.

## Related-work review plan (not yet done)
No novelty is claimed. Before any claim, review at minimum: (1) information-retrieval-based bug localization; (2) learning-based and LLM-based fault localization; (3) retrieval-augmented repository-level code generation/repair; (4) repository-level benchmarks (e.g., SWE-bench and variants); (5) code-graph representations for retrieval. Record for each: task, data, metric, reported numbers, and how they differ from this setup. Cite only works actually read.
