import { useEffect, useRef } from "react";
import Editor, { DiffEditor } from "@monaco-editor/react";
import "./monaco";
import { monaco } from "./monaco";

const LANG: Record<string, string> = { python: "python", javascript: "javascript", typescript: "typescript", tsx: "typescript", jsx: "javascript", json: "json", docs: "markdown", config: "ini" };
const theme = () => (document.documentElement.dataset.theme === "dark" ? "vs-dark" : "vs");

/** Read-only source viewer. `highlight` marks an exact line range and scrolls to it. */
export function CodeViewer({ value, language, highlight, height = 420 }: { value: string; language?: string; highlight?: [number, number] | null; height?: number }) {
  const ed = useRef<monaco.editor.IStandaloneCodeEditor | null>(null);
  const deco = useRef<monaco.editor.IEditorDecorationsCollection | null>(null);
  const apply = () => {
    if (!ed.current) return;
    deco.current?.clear();
    if (highlight) {
      deco.current = ed.current.createDecorationsCollection([{ range: new monaco.Range(highlight[0], 1, highlight[1], 1), options: { isWholeLine: true, className: "cm-hl", linesDecorationsClassName: "cm-hl-gutter" } }]);
      ed.current.revealLineInCenter(highlight[0]);
    }
  };
  useEffect(apply, [highlight?.[0], highlight?.[1], value]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Editor height={height} value={value} language={LANG[language ?? ""] ?? "plaintext"} theme={theme()}
      options={{ readOnly: true, minimap: { enabled: false }, fontSize: 12.5, fontFamily: "JetBrains Mono, monospace", scrollBeyondLastLine: false, renderLineHighlight: "none" }}
      onMount={(e) => { ed.current = e; apply(); }} />
  );
}

export function DiffViewer({ original, modified, language, height = 420 }: { original: string; modified: string; language?: string; height?: number }) {
  return <DiffEditor height={height} original={original} modified={modified} language={LANG[language ?? ""] ?? "plaintext"} theme={theme()}
    options={{ readOnly: true, renderSideBySide: true, minimap: { enabled: false }, fontSize: 12.5, scrollBeyondLastLine: false }} />;
}
