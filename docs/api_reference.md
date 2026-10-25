# API reference

Base path `/api/v1`. Interactive OpenAPI at `/docs`. JSON in/out.

**Errors:** `{"error":{"code","message","details"}}` (codes include `validation_error` 422, `not_found` 404, `conflict` 409, `not_indexed` 409, `security_violation` 422, `no_model_patch` 409, `forbidden` 403, `unauthorized` 401).
**Pagination:** `{items,total,limit,offset}` with `limit`/`offset` query params.
**Auth (optional):** `Authorization: Bearer <token>`; roles viewer < developer < admin. `CODEMIND_AUTH_REQUIRED=true` enables it.

| Method & path | Role | Purpose |
|---|---|---|
| GET `/health`, `/version` | public | liveness (no data), version |
| POST `/repositories` | developer | register `{path,name?,auto_index,ignore_patterns}` |
| GET `/repositories`, `/repositories/{id}` | viewer | list/detail (benchmark repos hidden) |
| POST `/repositories/{id}/index` | developer | `{mode: full|incremental}` → 202 job |
| GET `/repositories/{id}/index-status` | viewer | job + index version |
| POST `/repositories/{id}/index/cancel` | developer | cancel running job (404 if none) |
| GET `/repositories/{id}/files`, `/file?path&start&end`, `/symbols?q&kind` | viewer | structure and exact source lines |
| GET `/repositories/{id}/graph?level&language&directory&edge_type&min_confidence&include_tests&center&depth&max_nodes` | viewer | Cytoscape-style elements |
| GET `/repositories/{id}/graph/node/{node_id}`, `/graph/path?source&target`, `/graph/analysis` | viewer | node detail, path, components/centrality |
| POST `/search` | viewer | `{repository_id,query,mode: symbol|lexical|semantic|hybrid|dependency,limit,disabled_signals}` |
| POST/GET `/investigations`, GET `/investigations/{id}` | developer/viewer | bug report + stored results |
| POST `/investigations/{id}/retrieve` | developer | `{options:{method,weights,disabled_signals,top_k,budget_chars,graph_depth}}` → ranked candidates + context |
| POST `/investigations/{id}/analyze` | developer | `{use_model}`; returns `analysis` (not persisted), stores hypotheses |
| POST `/investigations/{id}/patches` | developer | `{diff, rationale}` or `{from_analysis:true}` (exactly one) |
| GET `/patches`, `/patches/{id}` | viewer | patch + verification report |
| POST `/patches/{id}/verify` | developer | sandboxed baseline → targeted → broad |
| POST `/patches/{id}/export` | developer | `{approve:true, apply_to_working_tree?}`; 403 without approval |
| POST `/experiments` | developer | create/run (see benchmark guide) → 202 |
| GET `/experiments`, `/experiments/{id}`, `/experiments/{id}/results?include_rows`, `/experiments/{id}/files/{name}` | viewer | status, summary, downloads |
| GET/PUT `/settings`, GET `/settings/status` | viewer/admin | non-secret settings; PUT validates, rejects unknown fields |
| GET `/dashboard` | viewer | overview data |
| GET `/audit`, POST `/users` | admin | audit trail; create user (token shown once) |
| POST `/demo/load` | developer | load labeled synthetic demo repo (idempotent) |
| WS `/ws/events` | viewer | `hello`, then `index.*`, `verify.*`, `experiment.*` events |
