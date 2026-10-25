export interface Page<T> { items: T[]; total: number; limit: number; offset: number }

export interface IndexJob {
  id: number; repository_id: number; mode: string; status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED";
  progress: number; total: number; message: string; error: string | null; stats: Record<string, any>;
}
export interface Repository {
  id: number; name: string; root_path: string; is_git: boolean; kind: "user" | "demo" | "benchmark"; status: string; fingerprint: string;
  languages: Record<string, { files: number; lines: number }>; index_version: number; file_count: number; last_indexed_at: string | null;
  latest_job: IndexJob | null; root_adjusted_to_git_root?: boolean;
}
export interface FileEntry { path: string; language: string; lines: number; is_test: boolean; parse_error: string | null }
export interface SymbolRow {
  id: number; node_id: string; kind: string; name: string; qualified_name: string; signature: string; docstring: string;
  path: string; language: string; start_line: number; end_line: number; is_test: boolean;
}
export interface GraphNode { data: { id: string; kind: string; label: string; path: string; start_line?: number; end_line?: number; language?: string } }
export interface GraphEdge { data: { id: string; source: string; target: string; type: string; resolution: string; confidence: number; provenance: string } }
export interface GraphResponse {
  elements: { nodes: GraphNode[]; edges: GraphEdge[] }; truncated: boolean; node_count: number; edge_count: number; level: string; unresolved_edge_count: number;
}
export interface NodeDetail {
  node: GraphNode["data"];
  callers: { node: GraphNode["data"]; edge_type: string; resolution: string; confidence: number }[];
  callees: { node: GraphNode["data"]; edge_type: string; resolution: string; confidence: number }[];
  dependents: { node: GraphNode["data"]; edge_type: string; resolution: string; confidence: number }[];
  dependencies: { node: GraphNode["data"]; edge_type: string; resolution: string; confidence: number }[];
  blast_radius: { node: GraphNode["data"]; hops: number }[];
  unresolved: { target: string | null; provenance: string; line: number | null }[];
}
export interface SearchHit {
  rank: number; path: string; symbol: string | null; start_line: number; end_line: number; score: number;
  signals: Record<string, number>; explanation: string[]; snippet: string | null;
}
export interface SearchResponse {
  run_id: number; mode: string; method: string; results: SearchHit[]; runtime_ms: number; index_version: number;
  active_signals: string[]; warnings: string[]; omitted_by_budget: number;
}
export interface Candidate {
  id: number; rank: number; file_path: string; symbol_name: string | null; start_line: number; end_line: number; score: number;
  signals: Record<string, number>; evidence: string[]; uncertainty: string[]; related_tests: string[]; confidence_label: "low" | "medium" | "high";
}
export interface Citation { path: string; start_line: number; end_line: number; reason?: string }
export interface Hypothesis {
  id: number; origin: "model" | "deterministic"; statement: string; supporting: string[]; contradicting: string[]; citations: Citation[];
  rejected_citations: Citation[]; unverified: string[]; model_name: string | null;
}
export interface Patch {
  id: number; investigation_id: number; status: string; changed_files: string[]; rationale: string; model_name: string | null;
  export_approved: boolean; exported_to: string | null; created_at: string; diff: string | null; report?: PatchReport;
}
export interface Execution {
  id: number; phase: "baseline" | "targeted" | "broad"; command: string; exit_code: number | null; runtime_seconds: number; timed_out: boolean;
  tests_total: number | null; tests_passed: number | null; tests_failed: number | null; failed_test_ids: string[]; outcome: string;
  sandbox_mode: string; environment: Record<string, any>;
}
export interface PatchReport {
  patch_id: number; status: string; changed_files: string[]; sandbox_modes: string[]; isolation: string[]; executions: Execution[];
  observed_facts: string[]; notes: string[]; possible_regression: boolean; verification_limits: string[]; export_approved: boolean; exported_to: string | null;
}
export interface Investigation {
  id: number; repository_id: number; title: string; status: string; created_at: string; failing_tests: string[];
  bug_report?: { description: string; stack_trace: string; error_message: string; failing_tests: string[]; extracted: Record<string, any> };
  retrieval_run?: { id: number; method: string; config: Record<string, any>; index_version: number; runtime_ms: number; context_chars: number } | null;
  candidates?: Candidate[]; hypotheses?: Hypothesis[]; patches?: Patch[]; evidence_notice?: string;
  analysis?: { origin: string; provider_available: boolean; model: string | null; summary: string; affected_tests: string[]; unverified: string[];
               iterations: number; errors: string[]; proposed_patch: { diff: string; rationale: string } | null };
  warnings?: string[]; context?: { used_chars: number; budget_chars: number; omitted_for_budget: number; snippets: any[] };
}
export interface Experiment {
  id: number; name: string; status: string; seed: number; progress: number; total: number; error: string | null; created_at: string;
  dataset: { name: string; kind: string; is_synthetic: boolean } | null; synthetic_notice: string | null; config?: Record<string, any>;
}
export interface MetricCI { mean: number; ci_low: number; ci_high: number }
export interface MethodSummary {
  n_tasks: number; top1?: MetricCI; top5?: MetricCI; mrr?: MetricCI; "recall@5"?: MetricCI; context_chars?: MetricCI; irrelevant_files?: MetricCI;
  latency_ms_median?: number; repair?: Record<string, number>; repair_skipped?: string;
}
export interface Paired { n: number; mean_diff: number; ci_low: number; ci_high: number; p_value: number; wins: number; losses: number; ties: number; interpretation: string }
export interface ExperimentSummary {
  experiment: string; n_tasks: number; seed: number; reference_method: string; methods: Record<string, MethodSummary>;
  paired_vs_reference: Record<string, Record<string, Paired>>; sample_size_warning: string | null; notes?: string[];
}
export interface ExperimentResults { experiment: Experiment; status: string; summary: ExperimentSummary | Record<string, never>; downloads: string[] }
export interface Dashboard {
  repositories: Repository[]; recent_investigations: { id: number; title: string; status: string; repository_id: number; created_at: string }[];
  active_indexing_jobs: { id: number; repository_id: number; progress: number; total: number; message: string }[];
  recent_verifications: { patch_id: number; investigation_id: number; status: string; files: string[]; created_at: string }[];
  experiments: { id: number; name: string; status: string; progress: number; total: number; created_at: string }[];
  counts: { repositories: number; investigations: number; test_executions: number };
}
export interface Settings {
  llm_provider: string; llm_model: string; llm_base_url: string; llm_api_key_configured: boolean; embedding_provider: string;
  retrieval_weights: Record<string, number>; context_budget_chars: number; graph_depth: number; sandbox_mode: string;
  sandbox_timeout_seconds: number; sandbox_memory_mb: number; sandbox_network: boolean; allow_local_sandbox: boolean; max_file_bytes: number; auth_required: boolean;
}
export interface SystemStatus {
  sandbox: { resolved_mode: string | null; explanation: string; docker_available: boolean; local_execution_allowed: boolean; isolation_note: string };
  embedding: { name: string; is_neural: boolean; note: string | null }; llm: { provider: string; configured: boolean };
  parsers: Record<string, string>; git_available: boolean;
}
