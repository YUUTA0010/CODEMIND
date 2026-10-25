import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAction, useApi } from "../hooks/useApi";
import { api } from "../services/api";
import type { Experiment, Page } from "../types/api";
import { Banner, Empty, ErrorBanner, Loading, PageHead, Panel, Progress, StatusMark, when } from "../components/ui";

export default function Research() {
  const list = useApi<Page<Experiment>>("/experiments?limit=100", { refreshOn: ["experiment."] });
  const nav = useNavigate();
  const [f, setF] = useState({ name: "", kind: "synthetic", path: "", repos_dir: "", seed: 0, ablate: true, repeats: 1, tune: false, testFraction: 0.5, repair: false, provideFailing: false });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<any>) => setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.type === "number" ? Number(e.target.value) : e.target.value });
  const start = useAction(async () => {
    const body: Record<string, unknown> = {
      name: f.name.trim() || `experiment ${new Date().toISOString().slice(0, 16)}`,
      dataset: { kind: f.kind, ...(f.kind !== "synthetic" ? { path: f.path } : {}), ...(f.kind === "swebench" && f.repos_dir ? { repos_dir: f.repos_dir } : {}) },
      seed: f.seed, repeats: f.repeats, provide_failing_tests: f.provideFailing, repair: { enabled: f.repair },
    };
    if (f.ablate) body.ablation_of = "dependency_aware_hybrid";
    if (f.tune) body.split = { test_fraction: f.testFraction, tune_weights: true };
    const e = await api.post<Experiment>("/experiments", body);
    nav(`/research/${e.id}`);
  });
  const items = list.data?.items ?? [];
  return (
    <>
      <PageHead title="Research lab" sub="Compare retrieval methods on a benchmark dataset with fixed seeds. Results can show improvement, no difference, or regression; small samples are reported as inconclusive." />
      <Panel title="Run experiment">
        <form className="col" onSubmit={(e) => { e.preventDefault(); start.run(); }}>
          <div className="row">
            <label className="f" style={{ flex: 1 }}>Name<input value={f.name} onChange={set("name")} placeholder="e.g. baseline vs dependency-aware" /></label>
            <label className="f">Dataset<select value={f.kind} onChange={set("kind")}><option value="synthetic">Synthetic (6 injected-bug tasks, pipeline test)</option><option value="directory">Local directory of tasks</option><option value="swebench">SWE-bench style local JSONL</option></select></label>
            <label className="f">Seed<input type="number" min={0} value={f.seed} onChange={set("seed")} style={{ width: 90 }} /></label>
            <label className="f">Repeats<input type="number" min={1} max={20} value={f.repeats} onChange={set("repeats")} style={{ width: 70 }} /></label>
          </div>
          {f.kind !== "synthetic" && <div className="row"><label className="f" style={{ flex: 1 }}>Dataset path (on the backend machine)<input value={f.path} onChange={set("path")} /></label>
            {f.kind === "swebench" && <label className="f" style={{ flex: 1 }}>Local repository clones directory<input value={f.repos_dir} onChange={set("repos_dir")} /></label>}</div>}
          <div className="row">
            <label className="row"><input type="checkbox" checked={f.ablate} onChange={set("ablate")} /> include ablations (drop one signal at a time)</label>
            <label className="row"><input type="checkbox" checked={f.tune} onChange={set("tune")} /> tune weights on a train split, evaluate on the held-out test split</label>
            {f.tune && <label className="row">test fraction <input type="number" step={0.05} min={0.1} max={0.9} value={f.testFraction} onChange={set("testFraction")} style={{ width: 70 }} /></label>}
            <label className="row"><input type="checkbox" checked={f.provideFailing} onChange={set("provideFailing")} /> give systems the failing test names</label>
            <label className="row"><input type="checkbox" checked={f.repair} onChange={set("repair")} /> repair stage (needs a configured model)</label>
          </div>
          <div className="row"><button className="primary" disabled={start.pending}>{start.pending ? "Starting…" : "Run experiment"}</button><span className="sub">Ground-truth labels are used only by the scoring code and are never given to the systems.</span></div>
          <ErrorBanner error={start.error} />
        </form>
      </Panel>
      {list.loading && !list.data ? <Loading /> : items.length === 0 ? <Panel><Empty title="No experiments yet">Run the synthetic dataset to check the pipeline end to end.</Empty></Panel> : (
        <Panel pad={false}><table className="t"><thead><tr><th>Name</th><th>Dataset</th><th>Status</th><th>Progress</th><th>Created</th></tr></thead><tbody>
          {items.map((e) => <tr key={e.id}><td><Link to={`/research/${e.id}`}>{e.name}</Link></td>
            <td>{e.dataset?.name}{e.dataset?.is_synthetic && <> <span className="mark warn">synthetic</span></>}</td>
            <td><StatusMark s={e.status} /></td><td style={{ minWidth: 120 }}>{e.status === "RUNNING" ? <Progress value={e.progress} max={e.total} /> : `${e.progress}/${e.total}`}</td><td>{when(e.created_at)}</td></tr>)}</tbody></table></Panel>)}
      <Banner kind="info">Planned related-work review and statistical protocol are documented in docs/research_methodology.md and docs/evaluation_protocol.md.</Banner>
    </>
  );
}
