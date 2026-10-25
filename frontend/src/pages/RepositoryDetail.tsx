import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useAction, useApi } from "../hooks/useApi";
import { api, qs } from "../services/api";
import type { GraphResponse, IndexJob, NodeDetail, Page, Repository, SymbolRow } from "../types/api";
import { Banner, Empty, ErrorBanner, EpistemicLegend, Loading, Mark, PageHead, Panel, Progress, StatusMark, Tabs, when } from "../components/ui";
import { CodeViewer } from "../components/CodeViewer";
import { GraphView } from "../components/GraphView";

interface FileList { files: { path: string; language: string; lines: number; is_test: boolean; parse_error: string | null }[]; language_counts: Record<string, number>; total: number }
interface FileContent { path: string; language: string; start_line: number; end_line: number; total_lines: number; content: string }
interface Analysis { components: { size: number; files: string[] }[]; degree_centrality: { path: string; centrality: number; in_degree: number; out_degree: number }[]; edge_counts?: unknown; [k: string]: unknown }

export default function RepositoryDetail() {
  const id = Number(useParams().id);
  const repo = useApi<Repository>(`/repositories/${id}`, { refreshOn: ["index."] });
  const [tab, setTab] = useState<"files" | "symbols" | "graph" | "analysis">("files");
  const [open, setOpen] = useState<{ path: string; line?: [number, number] } | null>(null);
  const start = useAction(async (mode: "full" | "incremental") => { await api.post<IndexJob>(`/repositories/${id}/index`, { mode }); repo.reload(); });
  const cancel = useAction(async () => { await api.post(`/repositories/${id}/index/cancel`); repo.reload(); });
  const r = repo.data;
  const job = r?.latest_job;
  const indexed = !!r && r.index_version > 0;
  return (
    <>
      <PageHead title={r?.name ?? "Repository"} sub={r && <span className="mono">{r.root_path}</span>}
        actions={<div className="row">
          <button className="primary" disabled={start.pending || job?.status === "RUNNING"} onClick={() => start.run(indexed ? "incremental" : "full")}>{indexed ? "Re-index (incremental)" : "Index"}</button>
          {indexed && <button disabled={start.pending || job?.status === "RUNNING"} onClick={() => start.run("full")}>Full re-index</button>}
          {job?.status === "RUNNING" && <button className="danger" onClick={() => cancel.run()}>Cancel</button>}
        </div>} />
      <ErrorBanner error={repo.error ?? start.error ?? cancel.error} />
      {!r ? <Loading /> : (
        <>
          {r.kind === "demo" && <Banner kind="demo">SYNTHETIC DEMO repository with an injected bug. Isolated from your repositories.</Banner>}
          <Panel title="Index status">
            <div className="row">
              <StatusMark s={r.status} />
              {job && <><StatusMark s={job.status} /><Progress value={job.progress} max={job.total} /><span className="mono">{job.progress}/{job.total} · {job.message}</span></>}
              <span>index version <b>{r.index_version}</b></span><span>indexed {when(r.last_indexed_at)}</span>
            </div>
            {job?.error && <Banner kind="error">{job.error}</Banner>}
            {job?.stats && Object.keys(job.stats).length > 0 && <p className="mono" style={{ margin: "8px 0 0" }}>{Object.entries(job.stats).filter(([, v]) => typeof v !== "object").map(([k, v]) => `${k}=${v}`).join("  ")}</p>}
            {indexed && <p className="sub" style={{ marginTop: 8 }}>Languages: {Object.entries(r.languages).map(([l, s]) => `${l} (${s.files} files, ${s.lines} lines)`).join(" · ")}</p>}
          </Panel>
          {!indexed ? <Panel><Empty title="Not indexed yet">Index this repository to browse files, symbols and the dependency graph.</Empty></Panel> : (
            <>
              <Tabs value={tab} onChange={setTab} tabs={[{ id: "files", label: "Files" }, { id: "symbols", label: "Symbols" }, { id: "graph", label: "Dependency graph" }, { id: "analysis", label: "Analysis" }]} />
              {tab === "files" && <Files id={id} open={open} setOpen={setOpen} />}
              {tab === "symbols" && <Symbols id={id} open={open} setOpen={setOpen} />}
              {tab === "graph" && <Graph id={id} setOpen={(o) => { setOpen(o); }} open={open} />}
              {tab === "analysis" && <AnalysisTab id={id} />}
            </>)}
        </>)}
    </>
  );
}

type OpenState = { path: string; line?: [number, number] } | null;
type Open = { open: OpenState; setOpen: (o: OpenState) => void };

function Source({ id, open }: { id: number; open: OpenState }) {
  const f = useApi<FileContent>(open ? `/repositories/${id}/file${qs({ path: open.path })}` : null);
  if (!open) return <Panel><Empty title="Select a file or symbol">Source is shown here with exact line numbers.</Empty></Panel>;
  return (
    <Panel title={<span className="mono">{open.path}{open.line ? `:${open.line[0]}-${open.line[1]}` : ""}</span>} actions={f.data && <span className="mono">{f.data.total_lines} lines</span>}>
      <ErrorBanner error={f.error} />
      {f.data ? <CodeViewer value={f.data.content} language={f.data.language} highlight={open.line ?? null} /> : f.loading ? <Loading /> : null}
    </Panel>
  );
}

function Files({ id, open, setOpen }: { id: number } & Open) {
  const q = useApi<FileList>(`/repositories/${id}/files`);
  const [filter, setFilter] = useState("");
  const files = useMemo(() => (q.data?.files ?? []).filter((f) => f.path.toLowerCase().includes(filter.toLowerCase())), [q.data, filter]);
  return (
    <div className="split">
      <Panel title={`Files (${q.data?.total ?? 0})`} pad={false}>
        <div style={{ padding: 8 }}><input style={{ width: "100%" }} placeholder="Filter by path" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter files" /></div>
        <div className="tree">{files.map((f) => <div key={f.path} className={open?.path === f.path ? "sel" : ""} onClick={() => setOpen({ path: f.path })} title={f.parse_error ?? ""}>{f.path} {f.is_test && <Mark kind="neutral">test</Mark>}{f.parse_error && <Mark kind="warn">parse error</Mark>}</div>)}</div>
        {q.data && files.length === 0 && <Empty title="No files match" />}
      </Panel>
      <Source id={id} open={open} />
    </div>
  );
}

function Symbols({ id, open, setOpen }: { id: number } & Open) {
  const [q, setQ] = useState(""); const [kind, setKind] = useState("");
  const res = useApi<Page<SymbolRow>>(`/repositories/${id}/symbols${qs({ q, kind, limit: 100 })}`);
  return (
    <div className="split" style={{ gridTemplateColumns: "minmax(320px, 440px) 1fr" }}>
      <Panel title={`Symbols (${res.data?.total ?? 0})`} pad={false}>
        <div className="row" style={{ padding: 8 }}>
          <input style={{ flex: 1 }} placeholder="Search names" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search symbols" />
          <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind"><option value="">all kinds</option><option>class</option><option>function</option><option>method</option><option>test</option></select>
        </div>
        <div className="tree">{res.data?.items.map((s) => <div key={s.id} className={open?.path === s.path && open.line?.[0] === s.start_line ? "sel" : ""} onClick={() => setOpen({ path: s.path, line: [s.start_line, s.end_line] })}>
          <b>{s.qualified_name}</b> <span style={{ color: "var(--ink-3)" }}>{s.kind} · {s.path}:{s.start_line}</span></div>)}</div>
        {res.data && res.data.items.length === 0 && <Empty title="No symbols match" />}
      </Panel>
      <Source id={id} open={open} />
    </div>
  );
}

function Graph({ id, open, setOpen }: { id: number } & Open) {
  const [level, setLevel] = useState<"file" | "symbol">("file");
  const [tests, setTests] = useState(true); const [minConf, setMinConf] = useState(0); const [dir, setDir] = useState("");
  const g = useApi<GraphResponse>(`/repositories/${id}/graph${qs({ level, include_tests: tests, min_confidence: minConf, directory: dir, max_nodes: 300 })}`);
  const [sel, setSel] = useState<string | null>(null);
  const detail = useApi<NodeDetail>(sel ? `/repositories/${id}/graph/node/${sel}` : null);
  const nodeLine = (n: NodeDetail["node"]) => n.path ? () => setOpen({ path: n.path, line: n.start_line ? [n.start_line, n.end_line ?? n.start_line] : undefined }) : undefined;
  return (
    <>
      <Panel>
        <div className="row">
          <label className="f">Level<select value={level} onChange={(e) => { setLevel(e.target.value as any); setSel(null); }}><option value="file">files</option><option value="symbol">symbols</option></select></label>
          <label className="f">Directory prefix<input value={dir} onChange={(e) => setDir(e.target.value)} placeholder="e.g. inventory/" /></label>
          <label className="f">Min confidence ({minConf.toFixed(1)})<input type="range" min={0} max={1} step={0.1} value={minConf} onChange={(e) => setMinConf(+e.target.value)} /></label>
          <label className="row"><input type="checkbox" checked={tests} onChange={(e) => setTests(e.target.checked)} /> include tests</label>
          {g.data && <span className="mono">{g.data.node_count} nodes · {g.data.edge_count} edges{g.data.truncated ? " · TRUNCATED to node cap" : ""} · {g.data.unresolved_edge_count} unresolved (not drawn)</span>}
        </div>
      </Panel>
      <ErrorBanner error={g.error} />
      <div className="split" style={{ gridTemplateColumns: "1fr 340px" }}>
        {g.data ? (g.data.node_count ? <GraphView graph={g.data} selected={sel} onSelect={setSel} /> : <Panel><Empty title="No nodes for these filters" /></Panel>) : <Loading />}
        <Panel title="Selected node" pad={!!sel}>
          {!sel ? <Empty title="Click a node">Callers, callees, dependents and blast radius appear here.</Empty> : !detail.data ? <Loading /> : (
            <div className="col">
              <div><b>{detail.data.node.label}</b> <Mark kind="neutral">{detail.data.node.kind}</Mark><br /><span className="mono">{detail.data.node.path}{detail.data.node.start_line ? `:${detail.data.node.start_line}` : ""}</span></div>
              {nodeLine(detail.data.node) && <button onClick={nodeLine(detail.data.node)}>Show source</button>}
              {([["Callers", detail.data.callers], ["Callees", detail.data.callees], ["Dependents", detail.data.dependents], ["Dependencies", detail.data.dependencies]] as const).map(([t, l]) => (
                <div key={t}><h3>{t} ({l.length})</h3>{l.length === 0 ? <span className="sub">none resolved</span> : <ul className="plain">{l.slice(0, 12).map((e, i) => <li key={i}><a href="#" onClick={(ev) => { ev.preventDefault(); setSel(e.node.id); }}>{e.node.label}</a> <Mark kind={e.resolution === "CONFIRMED" ? "fact" : "heuristic"}>{e.resolution.toLowerCase()} {e.confidence.toFixed(2)}</Mark></li>)}</ul>}</div>))}
              <div><h3>Blast radius ({detail.data.blast_radius.length})</h3><span className="sub">Nodes that transitively depend on this one (resolved edges only).</span></div>
              {detail.data.unresolved.length > 0 && <div><h3>Unresolved references ({detail.data.unresolved.length})</h3><ul className="plain">{detail.data.unresolved.slice(0, 8).map((u, i) => <li key={i} className="mono">{u.target ?? "?"} <span style={{ color: "var(--ink-3)" }}>line {u.line}</span></li>)}</ul></div>}
            </div>)}
        </Panel>
      </div>
      {open && <Source id={id} open={open} />}
    </>
  );
}

function AnalysisTab({ id }: { id: number }) {
  const a = useApi<Analysis>(`/repositories/${id}/graph/analysis`);
  if (a.error) return <ErrorBanner error={a.error} />;
  if (!a.data) return <Loading />;
  return (
    <>
      <p className="sub" style={{ marginBottom: 10 }}><Mark kind="fact">fact</Mark> Structural statistics computed from resolved edges only.</p>
      <div className="grid2">
        <Panel title="Most connected files (degree centrality)" pad={false}><table className="t"><thead><tr><th>File</th><th className="num">centrality</th><th className="num">in</th><th className="num">out</th></tr></thead><tbody>
          {a.data.degree_centrality.map((d) => <tr key={d.path}><td className="mono">{d.path}</td><td className="num">{d.centrality.toFixed(3)}</td><td className="num">{d.in_degree}</td><td className="num">{d.out_degree}</td></tr>)}</tbody></table></Panel>
        <Panel title="Connected components" pad={false}><table className="t"><thead><tr><th className="num">size</th><th>Files</th></tr></thead><tbody>
          {a.data.components.map((c, i) => <tr key={i}><td className="num">{c.size}</td><td className="mono">{c.files.slice(0, 4).join(", ")}{c.files.length > 4 ? ` … +${c.files.length - 4}` : ""}</td></tr>)}</tbody></table></Panel>
      </div>
      <EpistemicLegend />
      <Link to="/search">Search this repository</Link>
    </>
  );
}
