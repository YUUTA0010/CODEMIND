import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAction, useApi } from "../hooks/useApi";
import { api } from "../services/api";
import type { Investigation, Page, Repository } from "../types/api";
import { Empty, ErrorBanner, Loading, PageHead, Panel, StatusMark, when } from "../components/ui";

export default function Investigations() {
  const list = useApi<Page<Investigation>>("/investigations?limit=100");
  const repos = useApi<Page<Repository>>("/repositories?limit=100");
  const indexed = (repos.data?.items ?? []).filter((r) => r.index_version > 0);
  const [f, setF] = useState({ repo: "", title: "", description: "", stack_trace: "", failing: "" });
  const nav = useNavigate();
  const repoId = f.repo || (indexed[0] ? String(indexed[0].id) : "");
  const create = useAction(async () => {
    const inv = await api.post<Investigation>("/investigations", {
      repository_id: Number(repoId), title: f.title.trim(), description: f.description, stack_trace: f.stack_trace,
      failing_tests: f.failing.split("\n").map((s) => s.trim()).filter(Boolean),
    });
    nav(`/investigations/${inv.id}`);
  });
  const items = list.data?.items ?? [];
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<any>) => setF({ ...f, [k]: e.target.value });
  return (
    <>
      <PageHead title="Investigations" sub="Describe a bug, rank likely locations, review evidence, then optionally propose and verify a patch." />
      <Panel title="New investigation">
        <form className="col" onSubmit={(e) => { e.preventDefault(); create.run(); }}>
          <div className="row">
            <label className="f">Repository<select value={repoId} onChange={set("repo")}>{indexed.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label>
            <label className="f" style={{ flex: 1 }}>Title<input value={f.title} onChange={set("title")} placeholder="Short summary of the bug" /></label>
          </div>
          <label className="f">Bug report<textarea rows={4} value={f.description} onChange={set("description")} placeholder="What happens, and what should happen instead?" /></label>
          <label className="f">Stack trace or error output (optional)<textarea rows={4} value={f.stack_trace} onChange={set("stack_trace")} /></label>
          <label className="f">Failing tests, one per line (optional)<textarea rows={2} value={f.failing} onChange={set("failing")} placeholder="tests/test_pricing.py::test_discount_applies_at_threshold" /></label>
          <div className="row"><button className="primary" disabled={create.pending || !repoId || !f.title.trim() || !(f.description.trim() || f.stack_trace.trim() || f.failing.trim())}>{create.pending ? "Creating…" : "Create investigation"}</button>
            {indexed.length === 0 && repos.data && <span className="sub">Index a repository first.</span>}</div>
          <ErrorBanner error={create.error} />
        </form>
      </Panel>
      {list.loading && !list.data ? <Loading /> : items.length === 0 ? <Panel><Empty title="No investigations yet" /></Panel> : (
        <Panel pad={false}><table className="t"><thead><tr><th>Title</th><th>Status</th><th>Repository</th><th>Created</th></tr></thead><tbody>
          {items.map((i) => <tr key={i.id}><td><Link to={`/investigations/${i.id}`}>{i.title}</Link></td><td><StatusMark s={i.status} /></td><td>{i.repository_id}</td><td>{when(i.created_at)}</td></tr>)}</tbody></table></Panel>)}
    </>
  );
}
