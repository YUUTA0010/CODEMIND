# Benchmark guide

## Datasets (`dataset.kind`)
- **synthetic**: 6 injected-bug tasks on a small `inventory` package. A pipeline test only; always labeled SYNTHETIC in API and UI.
- **directory**: your own tasks, one folder per task:
  ```
  <root>/<task_key>/repo/       buggy repository snapshot
  <root>/<task_key>/task.json   {"description", "stack_trace", "relevant_tests", "ground_truth_files" (required), "ground_truth_symbols", "repo_revision"}
  ```
  The synthetic suite writes exactly this layout.
- **swebench**: a **local** JSONL in SWE-bench format plus `repos_dir` containing local clones; instance repos are materialized with `git archive`. Nothing is downloaded. Tested only on a synthetic JSONL.

Ground-truth labels (relevant files, tests, oracle fix) are used only by scoring code.

## Run
UI: **Research lab → Run experiment**. API:
```bash
curl -X POST localhost:8000/api/v1/experiments -H 'content-type: application/json' -d '{
  "name":"my run","dataset":{"kind":"directory","path":"/path/to/tasks"},
  "ablation_of":"dependency_aware_hybrid","seed":1,"repeats":3,
  "split":{"test_fraction":0.5,"tune_weights":true}}'
curl localhost:8000/api/v1/experiments/1/results
```
Custom methods: `"methods":[{"label":"x","method":"hybrid_dependency","weights":{"graph":0.3},"disabled_signals":["trace"]}]`.

## Outputs
Downloads from the experiment page or `GET /api/v1/experiments/{id}/files/{name}`. A sandbox must be available for the repair stage and for baseline reproduction; set `CODEMIND_ALLOW_LOCAL_SANDBOX=true` only for repositories you trust, or use Docker.
