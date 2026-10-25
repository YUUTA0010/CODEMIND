/** Bundle Monaco locally (no CDN) and wire its web workers through Vite. */
import * as monaco from "monaco-editor";
import { loader } from "@monaco-editor/react";
import editorWorker from "monaco-editor/editor/editor.worker?worker";

(self as any).MonacoEnvironment = { getWorker: () => new editorWorker() };
loader.config({ monaco });
export { monaco };
