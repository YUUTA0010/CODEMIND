import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAction, useApi } from "../hooks/useApi";
import { api } from "../services/api";
import type { Page, Repository } from "../types/api";
import { Banner, Empty, ErrorBanner, Loading, PageHead, Panel, Progress, StatusMark, when } from "../components/ui";

export default function Repositories() {
  const q = useApi<Page<Repository>>("/repositories?limit=100", { refreshOn: ["index."] });
  const [path, setPath] = useState("");
  const nav = useNavigate();
  const add = useAction(async () => {
    const r = await api.post<Repository>("/repositories", { path: path.trim(), auto_index: true });
    setPath(""); q.reload(); nav(`/repositories/${r.id}`);
  });
  const demo = useAction(async () => { await api.post("/demo/load"); q.reload(); });
  const items = q.data?.items ?? [];
  const hasDemo = items.some((r) => r.kind === "demo");
  return (
    <>
      <PageHead title="Repositories" sub="Register a folder on the machine running the backend. CodeMind reads it but never modifies it during indexing." />
      <Panel title="Add repository">
        <form className="row" onSubmit={(e) => { e.preventDefault(); if (path.trim()) add.run(); }}>
          <input style={{ flex: 1, minWidth: 260 }} placeholder="/path/to/your/project  or  C:\Users\you\project" value={path} onChange={(e) => setPath(e.target.value)} aria-label="Repository path" />
          <button className="primary" disabled={add.pending || !path.trim()}>{add.pending ? "Registering…" : "Register and index"}</button>
          {!hasDemo && <button type="button" disabled={demo.pending} onClick={() => demo.run()} title="Loads a clearly labelled synthetic repository with an injected bug">Load synthetic demo</button>}
        </form>
        <ErrorBanner error={add.error ?? demo.error} />
      </Panel>
      <ErrorBanner error={q.error} />
      {q.loading && !q.data ? <Loading /> : items.length === 0 ? <Panel><Empty title="No repositories registered">Enter a project path above, or load the synthetic demo to try the workflow.</Empty></Panel> : (
        <Panel pad={false}>
          <table className="t"><thead><tr><th>Name</th><th>Path</th><th>Status</th><th className="num">Files</th><th className="num">Index v</th><th>Last indexed</th><th /></tr></thead><tbody>
            {items.map((r) => (
              <tr key={r.id}>
                <td><Link to={`/repositories/${r.id}`}>{r.name}</Link> {r.kind === "demo" && <span className="mark warn">synthetic demo</span>}</td>
                <td className="mono">{r.root_path}</td>
                <td><StatusMark s={r.status} />{r.latest_job?.status === "RUNNING" && <Progress value={r.latest_job.progress} max={r.latest_job.total} />}</td>
                <td className="num">{r.file_count}</td><td className="num">{r.index_version}</td><td>{when(r.last_indexed_at)}</td>
                <td><Link to={`/repositories/${r.id}`}>open</Link></td>
              </tr>))}
          </tbody></table>
        </Panel>)}
      {items.some((r) => r.kind === "demo") && <Banner kind="demo">The demo repository is synthetic test data. Its results are isolated from your own repositories and are not evidence about real-world performance.</Banner>}
    </>
  );
}
