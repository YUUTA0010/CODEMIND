import { useEffect, useState } from "react";
import { useAction, useApi } from "../hooks/useApi";
import { api } from "../services/api";
import type { Settings, SystemStatus } from "../types/api";
import { Banner, ErrorBanner, Loading, Mark, PageHead, Panel } from "../components/ui";

const SIGNALS = ["lexical", "symbol_exact", "semantic", "graph", "test", "trace", "history"];

export default function SettingsPage() {
  const q = useApi<Settings>("/settings");
  const st = useApi<SystemStatus>("/settings/status");
  const [d, setD] = useState<Settings | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => { if (q.data) setD(q.data); }, [q.data]);
  const save = useAction(async () => {
    if (!d) return;
    const out = await api.put<Settings>("/settings", {
      llm_provider: d.llm_provider, llm_model: d.llm_model, llm_base_url: d.llm_base_url, embedding_provider: d.embedding_provider, retrieval_weights: d.retrieval_weights,
      context_budget_chars: d.context_budget_chars, graph_depth: d.graph_depth, sandbox_timeout_seconds: d.sandbox_timeout_seconds, sandbox_memory_mb: d.sandbox_memory_mb, sandbox_network: d.sandbox_network,
    });
    setD(out); setSaved(true); st.reload();
  });
  if (!d) return <>{q.error ? <ErrorBanner error={q.error} /> : <Loading />}</>;
  const up = (patch: Partial<Settings>) => { setSaved(false); setD({ ...d, ...patch }); };
  const sb = st.data?.sandbox;
  return (
    <>
      <PageHead title="Settings" sub="Non-secret runtime settings. API keys are read from environment variables only; they are never stored or shown here." />
      <ErrorBanner error={save.error} />
      {saved && <Banner kind="info">Saved.</Banner>}
      <div className="grid2">
        <Panel title="Model provider">
          <div className="col">
            <label className="f">Provider<select value={d.llm_provider} onChange={(e) => up({ llm_provider: e.target.value })}><option value="none">none (deterministic analysis only)</option><option value="openai_compatible">OpenAI-compatible</option><option value="anthropic">Anthropic</option></select></label>
            <label className="f">Model<input value={d.llm_model} onChange={(e) => up({ llm_model: e.target.value })} /></label>
            <label className="f">Base URL (OpenAI-compatible)<input value={d.llm_base_url} onChange={(e) => up({ llm_base_url: e.target.value })} /></label>
            <p className="sub">API key: {d.llm_api_key_configured ? <Mark kind="pass">configured via CODEMIND_LLM_API_KEY</Mark> : <Mark kind="warn">not set. Export CODEMIND_LLM_API_KEY before starting the backend.</Mark>}</p>
          </div>
        </Panel>
        <Panel title="Embeddings">
          <label className="f">Provider<select value={d.embedding_provider} onChange={(e) => up({ embedding_provider: e.target.value })}><option value="hashing">hashing (offline, not neural)</option><option value="openai_compatible">OpenAI-compatible embeddings</option></select></label>
          {st.data && <p className="sub" style={{ marginTop: 8 }}>Active: {st.data.embedding.name} · {st.data.embedding.is_neural ? "neural" : "not neural"}. {st.data.embedding.note}</p>}
        </Panel>
      </div>
      <Panel title="Retrieval weights" actions={<span className="sub">Normalized at query time. History defaults to 0.</span>}>
        <div className="row">{SIGNALS.map((s) => <label className="f" key={s}>{s}<input type="number" step={0.05} min={0} max={1} style={{ width: 90 }} value={d.retrieval_weights[s] ?? 0} onChange={(e) => up({ retrieval_weights: { ...d.retrieval_weights, [s]: Number(e.target.value) } })} /></label>)}</div>
        <div className="row" style={{ marginTop: 10 }}>
          <label className="f">Context budget (chars)<input type="number" min={1000} step={1000} value={d.context_budget_chars} onChange={(e) => up({ context_budget_chars: Number(e.target.value) })} /></label>
          <label className="f">Graph depth<input type="number" min={0} max={4} value={d.graph_depth} onChange={(e) => up({ graph_depth: Number(e.target.value) })} style={{ width: 80 }} /></label>
        </div>
      </Panel>
      <Panel title="Sandbox policy">
        <div className="row">
          <label className="f">Timeout (s)<input type="number" min={5} value={d.sandbox_timeout_seconds} onChange={(e) => up({ sandbox_timeout_seconds: Number(e.target.value) })} /></label>
          <label className="f">Memory (MB)<input type="number" min={128} step={128} value={d.sandbox_memory_mb} onChange={(e) => up({ sandbox_memory_mb: Number(e.target.value) })} /></label>
          <label className="row"><input type="checkbox" checked={d.sandbox_network} onChange={(e) => up({ sandbox_network: e.target.checked })} /> allow network inside the sandbox</label>
        </div>
        {sb && <><p style={{ marginTop: 10 }}>Resolved mode: <b>{sb.resolved_mode ?? "none"}</b>. {sb.explanation}</p><p className="sub">{sb.isolation_note}</p></>}
        <p className="sub">Local (non-Docker) execution is enabled only by starting the backend with CODEMIND_ALLOW_LOCAL_SANDBOX=true: currently <b>{d.allow_local_sandbox ? "on" : "off"}</b>. It is a restricted subprocess, not a strong isolation boundary.</p>
      </Panel>
      <div className="row"><button className="primary" disabled={save.pending} onClick={() => save.run()}>Save settings</button><span className="sub">Authentication: {d.auth_required ? "required" : "off (local use only)"}</span></div>
    </>
  );
}
