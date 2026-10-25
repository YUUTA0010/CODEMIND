import { useEffect, useRef } from "react";
import cytoscape from "cytoscape";
import type { GraphResponse } from "../types/api";

const KIND_COLOR: Record<string, string> = { file: "#2457b5", test_file: "#17803d", class: "#7a3b8f", function: "#0d7a68", method: "#0d7a68", test: "#17803d", module: "#2457b5" };

/** Cytoscape dependency graph. Unresolved/inferred edges are drawn dashed; node click reports the node id. */
export function GraphView({ graph, selected, onSelect, height = 520 }: { graph: GraphResponse; selected: string | null; onSelect: (id: string) => void; height?: number }) {
  const el = useRef<HTMLDivElement>(null);
  const cy = useRef<cytoscape.Core | null>(null);
  useEffect(() => {
    if (!el.current) return;
    const dark = document.documentElement.dataset.theme === "dark";
    const c = cytoscape({
      container: el.current,
      elements: [...graph.elements.nodes, ...graph.elements.edges].map((e) => ({ data: e.data })),
      layout: { name: "cose", animate: false, nodeRepulsion: () => 60000, idealEdgeLength: () => 130, padding: 30 } as any,
      style: [
        { selector: "node", style: { label: "data(label)", "font-size": 8, color: dark ? "#cdd6df" : "#18212b", "text-valign": "bottom", "text-margin-y": 3, width: 14, height: 14,
          "background-color": ((n: any) => KIND_COLOR[n.data("kind")] ?? "#7a8691") as any } },
        { selector: "node:selected", style: { "border-width": 3, "border-color": "#e0a800" } },
        { selector: "edge", style: { width: 1.2, "line-color": dark ? "#4a5663" : "#9aa5b0", "target-arrow-color": dark ? "#4a5663" : "#9aa5b0", "target-arrow-shape": "triangle", "curve-style": "bezier", "arrow-scale": 0.8 } },
        { selector: "edge[resolution = 'INFERRED']", style: { "line-style": "dashed" } },
        { selector: "edge[type = 'TESTS']", style: { "line-color": "#17803d", "target-arrow-color": "#17803d" } },
      ],
    });
    c.on("tap", "node", (ev) => onSelect(ev.target.id()));
    cy.current = c;
    return () => { c.destroy(); cy.current = null; };
  }, [graph]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const c = cy.current; if (!c) return;
    c.$(":selected").unselect();
    if (selected) { const n = c.getElementById(selected); if (n.nonempty()) { n.select(); c.animate({ center: { eles: n } }, { duration: 150 }); } }
  }, [selected, graph]);
  return <div ref={el} style={{ height, border: "1px solid var(--rule)", background: "var(--panel)" }} role="img" aria-label="Dependency graph" />;
}
