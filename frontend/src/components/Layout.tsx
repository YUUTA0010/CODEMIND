import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useApi } from "../hooks/useApi";
import { onConnection } from "../services/events";
import { getToken, setToken } from "../services/api";
import type { Dashboard, SystemStatus } from "../types/api";

const links: [string, string][] = [
  ["/", "Overview"], ["/repositories", "Repositories"], ["/search", "Search"], ["/investigations", "Investigations"], ["/research", "Research lab"], ["/settings", "Settings"],
];

export default function Layout() {
  const [live, setLive] = useState(false);
  const [dark, setDark] = useState(() => { try { return localStorage.getItem("codemind.theme") === "dark"; } catch { return false; } });
  const status = useApi<SystemStatus>("/settings/status");
  const dash = useApi<Dashboard>("/dashboard", { refreshOn: ["index.", "verify.", "experiment."] });
  useEffect(() => onConnection(setLive), []);
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    try { localStorage.setItem("codemind.theme", dark ? "dark" : "light"); } catch { /* ignore */ }
  }, [dark]);
  const sb = status.data?.sandbox;
  const running = dash.data?.active_indexing_jobs.length ?? 0;
  return (
    <div className="app">
      <header className="strip">
        <b>CodeMind</b>
        <span><span className={`dot ${live ? "on" : "bad"}`} />{live ? "live events" : "events offline"}</span>
        <span><span className={`dot ${sb?.resolved_mode ? (sb.resolved_mode === "docker" ? "on" : "warn") : "bad"}`} />sandbox: {sb ? (sb.resolved_mode ?? "none (verification unavailable)") : "…"}</span>
        <span><span className="dot" />embeddings: {status.data ? `${status.data.embedding.name}${status.data.embedding.is_neural ? "" : " (not neural)"}` : "…"}</span>
        <span><span className={`dot ${status.data?.llm.configured ? "on" : ""}`} />model: {status.data ? (status.data.llm.configured ? status.data.llm.provider : "none configured") : "…"}</span>
        {running > 0 && <span><span className="dot warn" />{running} indexing</span>}
        <span className="sp" />
        {status.error && <span style={{ color: "#ff9088" }}>backend unreachable</span>}
        <button style={{ padding: "1px 8px", fontSize: 12 }} onClick={() => { const t = window.prompt("API token (leave empty to clear)", getToken()); if (t !== null) { setToken(t.trim()); location.reload(); } }}>token</button>
        <button style={{ padding: "1px 8px", fontSize: 12 }} onClick={() => setDark(!dark)} aria-label="Toggle theme">{dark ? "light" : "dark"}</button>
      </header>
      <nav className="nav" aria-label="Main">
        {links.map(([to, label]) => <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => (isActive ? "active" : "")}>{label}</NavLink>)}
      </nav>
      <main className="main"><Outlet /></main>
    </div>
  );
}
