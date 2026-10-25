"""Resolve parsed references into typed, provenance-labelled dependency edges.

Resolution labels
-----------------
CONFIRMED  the target is determined by lexical scope, an import binding, or `self`/`this` member lookup.
INFERRED   plausible but not proven (unique-name heuristics, test->target conventions, default exports).
UNRESOLVED the target is external, dynamic, or ambiguous; no concrete target is guessed. The edge keeps the
           unresolved text so users can see what the analyzer could not resolve.

Known limitations: no type inference, so `obj.method()` on an arbitrary receiver is never CONFIRMED; local variables
that shadow imports are not tracked; `getattr`, decorators that rebind names, and metaprogramming are invisible.
"""
from __future__ import annotations

import builtins
import posixpath
from dataclasses import dataclass, field

from app.parsing.ir import ParsedFile

PY_BUILTINS = set(dir(builtins))
JS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
SELF_NAMES = {"self", "cls", "this"}


@dataclass
class SymMeta:
    id: int
    file_id: int
    kind: str
    name: str
    qname: str
    parent_qname: str | None
    is_test: bool
    bases: list[str]


@dataclass
class FileInfo:
    file_id: int
    path: str
    language: str
    parsed: ParsedFile
    sym_ids: dict[str, int]  # qualified_name -> symbol id
    is_test: bool = False


@dataclass
class Binding:
    kind: str  # symbol | module | external | ambiguous
    file_id: int | None = None
    symbol_id: int | None = None
    text: str = ""
    confidence: float = 1.0
    provenance: str = ""


@dataclass
class _Edge:
    edge_type: str
    src_file_id: int | None
    src_symbol_id: int | None
    dst_file_id: int | None
    dst_symbol_id: int | None
    resolution: str
    confidence: float
    provenance: str
    unresolved_target: str | None = None
    line: int | None = None

    def key(self):
        return (self.edge_type, self.src_file_id, self.src_symbol_id, self.dst_file_id, self.dst_symbol_id, self.unresolved_target)

    def to_dict(self) -> dict:
        return {
            "edge_type": self.edge_type, "src_file_id": self.src_file_id, "src_symbol_id": self.src_symbol_id,
            "dst_file_id": self.dst_file_id, "dst_symbol_id": self.dst_symbol_id, "resolution": self.resolution,
            "confidence": round(self.confidence, 3), "provenance": self.provenance,
            "unresolved_target": self.unresolved_target, "line": self.line,
        }


class Resolver:
    def __init__(self, files: list[FileInfo], symbols: list[SymMeta]):
        self.files = {f.file_id: f for f in files}
        self.by_path = {f.path: f for f in files}
        self.sym = {s.id: s for s in symbols}
        self.top_level: dict[int, dict[str, int]] = {}
        self.members: dict[tuple[int, str], dict[str, int]] = {}
        self.by_name: dict[str, list[int]] = {}
        self.qname_index: dict[tuple[int, str], int] = {}
        for s in symbols:
            self.qname_index[(s.file_id, s.qname)] = s.id
            if s.kind == "module":
                continue
            if s.parent_qname is None:
                self.top_level.setdefault(s.file_id, {}).setdefault(s.name, s.id)
            else:
                self.members.setdefault((s.file_id, s.parent_qname), {}).setdefault(s.name, s.id)
            if s.kind in ("function", "method", "class"):
                self.by_name.setdefault(s.name, []).append(s.id)
        self.module_symbol = {s.file_id: s.id for s in symbols if s.kind == "module"}
        self.py_suffix: dict[str, list[str]] = {}
        for f in files:
            if f.language == "python":
                for dotted in self._py_dotted_names(f.path):
                    self.py_suffix.setdefault(dotted, []).append(f.path)
        self._bindings: dict[int, tuple[dict[str, Binding], dict[str, Binding]]] = {}
        self.edges: dict[tuple, _Edge] = {}

    # ------------------------------------------------------------------ module path resolution
    @staticmethod
    def _py_dotted_names(path: str) -> list[str]:
        stem = path[:-3] if path.endswith(".py") else path[:-4] if path.endswith(".pyi") else path
        parts = stem.split("/")
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return [".".join(parts[i:]) for i in range(len(parts))] if parts else []

    def _py_module(self, importer: str, module: str, level: int) -> tuple[str | None, float, str]:
        """-> (path | None, confidence, note). path None means unresolved; note says why."""
        if level > 0:
            base = posixpath.dirname(importer)
            for _ in range(level - 1):
                base = posixpath.dirname(base)
            rel = posixpath.join(base, module.replace(".", "/")) if module else base
            cands = ((rel + ".py", rel + "/__init__.py", rel + ".pyi") if module else (rel + "/__init__.py",))
            for cand in cands:
                if cand.lstrip("/") in self.by_path:
                    return cand.lstrip("/"), 1.0, "relative import"
            return None, 0.0, "relative import target not found"
        rel = module.replace(".", "/")
        for cand in (rel + ".py", rel + "/__init__.py"):
            if cand in self.by_path:
                return cand, 1.0, "exact repo-relative module path"
        sib = posixpath.join(posixpath.dirname(importer), rel)
        for cand in (sib + ".py", sib + "/__init__.py"):
            if cand.lstrip("/") in self.by_path:
                return cand.lstrip("/"), 0.95, "sibling module (script-style import)"
        if "." in module:  # multi-part suffix match supports src/ or backend/ layouts
            paths = self.py_suffix.get(module, [])
            if len(paths) == 1:
                return paths[0], 0.9, "unique source-root suffix match"
            if len(paths) > 1:
                return None, 0.0, f"ambiguous module ({len(paths)} candidates)"
        return None, 0.0, "external or unresolvable module"

    def _js_module(self, importer: str, spec: str) -> tuple[str | None, float, str]:
        if not spec.startswith("."):
            return None, 0.0, "bare specifier (external package or path alias)"
        base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
        if base.startswith(".."):
            return None, 0.0, "outside repository"
        cands = [base]
        stem, ext = posixpath.splitext(base)
        if ext in (".js", ".jsx", ".mjs", ".cjs"):  # TS ESM convention: import './x.js' -> x.ts
            cands += [stem + ".ts", stem + ".tsx"]
        cands += [base + e for e in JS_EXTS] + [base + "/index" + e for e in JS_EXTS]
        for c in cands:
            if c in self.by_path:
                return c, 1.0, "relative path resolved"
        return None, 0.0, "relative import target not found"

    def resolve_module(self, importer: FileInfo, module: str, level: int) -> tuple[str | None, float, str]:
        if importer.language == "python":
            return self._py_module(importer.path, module, level)
        return self._js_module(importer.path, module)

    # ------------------------------------------------------------------ bindings
    def _file_bindings(self, f: FileInfo, depth: int = 0) -> tuple[dict[str, Binding], dict[str, Binding]]:
        """-> (name bindings, module-alias bindings) created by the file's imports."""
        if f.file_id in self._bindings:
            return self._bindings[f.file_id]
        names: dict[str, Binding] = {}
        mods: dict[str, Binding] = {}
        self._bindings[f.file_id] = (names, mods)  # pre-register to stop cycles
        for imp in f.parsed.imports:
            path, conf, note = self.resolve_module(f, imp.module, imp.level)
            target = self.by_path.get(path) if path else None
            ext_text = ("." * imp.level) + imp.module
            alias = imp.namespace_alias
            if alias:
                # Python `import a.b.c` (no `as`) binds `a` but code refers to `a.b.c.func`, so key by the full dotted text.
                dotted_unaliased = f.language == "python" and "." in imp.module and alias == imp.module.split(".")[0]
                key = imp.module if dotted_unaliased else alias
                if target:
                    mods[key] = Binding("module", target.file_id, None, ext_text, conf, note)
                else:
                    mods[key] = Binding("external", None, None, ext_text, 0.0, note)
            if imp.kind in ("from", "reexport") or imp.names:
                for orig, local in imp.names:
                    if orig == "*":
                        continue
                    names[local] = self._bind_name(f, target, orig, imp, conf, note, ext_text, depth)
                    if names[local].kind == "module":  # `from pkg import submodule` -> usable as `submodule.func()`
                        mods[local] = names[local]
        return names, mods

    def _bind_name(self, f: FileInfo, target: FileInfo | None, orig: str, imp, conf: float, note: str, ext_text: str, depth: int) -> Binding:
        if target is None:
            # `from pkg import submodule` where pkg itself resolved is handled below; here pkg is unresolved
            if f.language == "python":
                sub, c2, n2 = self._py_module(f.path, (imp.module + "." if imp.module else "") + orig, imp.level)
                if sub:
                    return Binding("module", self.by_path[sub].file_id, None, ext_text, c2, n2)
            return Binding("external", None, None, f"{ext_text}.{orig}" if f.language == "python" else f"{ext_text}:{orig}", 0.0, note)
        if orig == "default":
            tl = [sid for sid in self.top_level.get(target.file_id, {}).values() if self.sym[sid].kind in ("class", "function")]
            if len(tl) == 1:
                return Binding("symbol", target.file_id, tl[0], ext_text, 0.6, "default export (single top-level symbol)")
            return Binding("module", target.file_id, None, ext_text, 0.6, "default export (file-level only)")
        sid = self.top_level.get(target.file_id, {}).get(orig)
        if sid is not None:
            return Binding("symbol", target.file_id, sid, ext_text, min(conf, 0.98), "import binding")
        if f.language == "python":  # submodule import: from pkg import mod
            base = posixpath.dirname(target.path) if target.path.endswith("__init__.py") else None
            if base is not None:
                for cand in (f"{base}/{orig}.py", f"{base}/{orig}/__init__.py"):
                    if cand in self.by_path:
                        return Binding("module", self.by_path[cand].file_id, None, ext_text, 0.95, "submodule import")
        if depth < 3 and target.file_id != f.file_id:  # follow re-exports (e.g. package __init__)
            tnames, _ = self._file_bindings(target, depth + 1)
            b = tnames.get(orig)
            if b is not None and b.kind in ("symbol", "module"):
                return Binding(b.kind, b.file_id, b.symbol_id, ext_text, min(conf, b.confidence, 0.9), "via re-export")
        return Binding("module", target.file_id, None, ext_text, conf * 0.5, f"name '{orig}' not found in target module")

    # ------------------------------------------------------------------ edge helpers
    def _add(self, e: _Edge) -> None:
        k = e.key()
        old = self.edges.get(k)
        if old is None or e.confidence > old.confidence:
            if old is not None and old.line is not None and e.line is None:
                e.line = old.line
            self.edges[k] = e

    def _src_symbol(self, f: FileInfo, qname: str) -> int | None:
        if not qname:
            return self.module_symbol.get(f.file_id)
        sid = f.sym_ids.get(qname)
        return sid if sid is not None else self.module_symbol.get(f.file_id)

    def _enclosing_class(self, f: FileInfo, qname: str) -> SymMeta | None:
        parts = qname.split(".") if qname else []
        for i in range(len(parts), 0, -1):
            sid = f.sym_ids.get(".".join(parts[:i]))
            if sid is not None and self.sym[sid].kind == "class":
                return self.sym[sid]
        return None

    def _method_in_hierarchy(self, f: FileInfo, cls: SymMeta, name: str, seen: set[int] | None = None) -> tuple[int | None, bool]:
        """-> (symbol id, found_directly_in_class)."""
        seen = seen or set()
        if cls.id in seen:
            return None, False
        seen.add(cls.id)
        sid = self.members.get((cls.file_id, cls.qname), {}).get(name)
        if sid is not None:
            return sid, True
        owner = self.files[cls.file_id]
        for base in cls.bases:
            bsym = self._resolve_class_name(owner, base)
            if bsym is not None:
                found, _ = self._method_in_hierarchy(owner, bsym, name, seen)
                if found is not None:
                    return found, False
        return None, False

    def _resolve_class_name(self, f: FileInfo, text: str) -> SymMeta | None:
        names, mods = self._file_bindings(f)
        if "." not in text:
            sid = self.top_level.get(f.file_id, {}).get(text)
            if sid is not None and self.sym[sid].kind == "class":
                return self.sym[sid]
            b = names.get(text)
            if b and b.kind == "symbol" and b.symbol_id is not None and self.sym[b.symbol_id].kind == "class":
                return self.sym[b.symbol_id]
            return None
        head, _, tail = text.rpartition(".")
        mb = self._module_for_prefix(f, head, mods)
        if mb and mb.file_id is not None and "." not in tail:
            sid = self.top_level.get(mb.file_id, {}).get(tail)
            if sid is not None and self.sym[sid].kind == "class":
                return self.sym[sid]
        return None

    @staticmethod
    def _module_for_prefix(f: FileInfo, prefix: str, mods: dict[str, Binding]) -> Binding | None:
        return mods.get(prefix)

    def _name_only(self, src_f: FileInfo, src_sym: int | None, method: str, line: int, text: str, edge_type: str = "CALLS") -> None:
        cands = [s for s in self.by_name.get(method, []) if self.sym[s].kind in ("method", "function") and s != src_sym]
        if len(cands) == 1:
            t = self.sym[cands[0]]
            self._add(_Edge(edge_type, src_f.file_id, src_sym, t.file_id, t.id, "INFERRED", 0.3,
                            "name-only match: receiver type unknown, single repo-wide candidate", None, line))
        else:
            why = f"ambiguous name-only call ({len(cands)} candidates)" if cands else "dynamic/external receiver"
            self._add(_Edge(edge_type, src_f.file_id, src_sym, None, None, "UNRESOLVED", 0.0, why, text, line))

    # ------------------------------------------------------------------ main resolution
    def resolve_all(self) -> list[dict]:
        for f in self.files.values():
            self._resolve_file(f)
        self._derive_tests()
        self._derive_depends_on()
        return [e.to_dict() for e in self.edges.values()]

    def _resolve_file(self, f: FileInfo) -> None:
        names, mods = self._file_bindings(f)
        # DEFINES
        mod_id = self.module_symbol.get(f.file_id)
        if mod_id is not None:
            self._add(_Edge("DEFINES", f.file_id, None, f.file_id, mod_id, "CONFIRMED", 1.0, "module symbol"))
        for s in f.parsed.symbols:
            if s.kind == "module":
                continue
            sid = f.sym_ids.get(s.qualified_name)
            if sid is None:
                continue
            if s.parent_qname is None:
                self._add(_Edge("DEFINES", f.file_id, None, f.file_id, sid, "CONFIRMED", 1.0, "top-level definition", None, s.start_line))
            else:
                pid = f.sym_ids.get(s.parent_qname)
                if pid is not None:
                    self._add(_Edge("DEFINES", f.file_id, pid, f.file_id, sid, "CONFIRMED", 1.0, "nested definition", None, s.start_line))
        # IMPORTS (file -> file, and file -> symbol for named imports)
        for imp in f.parsed.imports:
            path, conf, note = self.resolve_module(f, imp.module, imp.level)
            target = self.by_path.get(path) if path else None
            label = ("." * imp.level) + imp.module
            if target is None:
                self._add(_Edge("IMPORTS", f.file_id, None, None, None, "UNRESOLVED", 0.0, note, label, imp.line))
                continue
            self._add(_Edge("IMPORTS", f.file_id, None, target.file_id, None, "CONFIRMED", conf, note, None, imp.line))
            for orig, local in imp.names:
                b = names.get(local)
                if b and b.kind == "symbol" and b.symbol_id is not None:
                    self._add(_Edge("IMPORTS", f.file_id, None, b.file_id, b.symbol_id,
                                    "CONFIRMED" if b.confidence >= 0.9 else "INFERRED", b.confidence, b.provenance, None, imp.line))
        # INHERITS
        for s in f.parsed.symbols:
            if s.kind != "class":
                continue
            sid = f.sym_ids.get(s.qualified_name)
            for base in s.bases:
                target = self._resolve_class_name(f, base)
                if target is not None:
                    self._add(_Edge("INHERITS", f.file_id, sid, target.file_id, target.id, "CONFIRMED", 1.0, "base class resolved", None, s.start_line))
                elif base.split(".")[-1] not in PY_BUILTINS:
                    self._add(_Edge("INHERITS", f.file_id, sid, None, None, "UNRESOLVED", 0.0, "external or unresolvable base class", base, s.start_line))
        # CALLS / REFERENCES
        for r in f.parsed.references:
            src = self._src_symbol(f, r.source_qname)
            if r.kind == "call":
                self._resolve_call(f, src, r.source_qname, r.target_text, r.line, names, mods)
            else:
                self._resolve_reference(f, src, r.source_qname, r.target_text, r.line, names)

    def _lexical_lookup(self, f: FileInfo, scope_qname: str, name: str) -> int | None:
        parts = scope_qname.split(".") if scope_qname else []
        for i in range(len(parts), -1, -1):
            prefix = ".".join(parts[:i])
            cand = f"{prefix}.{name}" if prefix else name
            sid = f.sym_ids.get(cand)
            if sid is not None:
                # methods are not lexically visible from sibling methods (Python class scope), so skip them
                if self.sym[sid].kind == "method":
                    continue
                return sid
        return None

    def _resolve_call(self, f, src, scope, text, line, names, mods) -> None:
        if text.startswith("<dynamic>."):
            self._name_only(f, src, text.split(".", 1)[1], line, text)
            return
        parts = text.split(".")
        if len(parts) == 1:
            name = parts[0]
            sid = self._lexical_lookup(f, scope, name)
            if sid is not None:
                if sid != src:
                    t = self.sym[sid]
                    self._add(_Edge("CALLS", f.file_id, src, t.file_id, t.id, "CONFIRMED", 1.0, "lexical scope / same-file definition", None, line))
                else:
                    self._add(_Edge("CALLS", f.file_id, src, f.file_id, sid, "CONFIRMED", 1.0, "recursive call", None, line))
                return
            b = names.get(name)
            if b is not None:
                self._call_binding(f, src, b, line, text)
                return
            if f.language == "python" and name in PY_BUILTINS:
                return
            if f.language != "python" and name in {"require", "setTimeout", "setInterval", "fetch", "parseInt", "parseFloat", "String", "Number", "Boolean", "Array", "Object", "Promise", "Error", "Symbol", "Date", "Map", "Set", "RegExp", "JSON", "Math", "console", "describe", "it", "test", "expect", "beforeEach", "afterEach", "beforeAll", "afterAll", "jest", "vi"}:
                return
            self._add(_Edge("CALLS", f.file_id, src, None, None, "UNRESOLVED", 0.0, "name not defined in scope or imports", text, line))
            return
        head = parts[0]
        method = parts[-1]
        if head in SELF_NAMES:
            cls = self._enclosing_class(f, scope)
            if len(parts) == 2 and cls is not None:
                sid, direct = self._method_in_hierarchy(f, cls, method)
                if sid is not None:
                    t = self.sym[sid]
                    self._add(_Edge("CALLS", f.file_id, src, t.file_id, sid, "CONFIRMED" if direct else "INFERRED",
                                    0.95 if direct else 0.8, "self/this method in own class" if direct else "method found via base-class chain", None, line))
                    return
                self._add(_Edge("CALLS", f.file_id, src, None, None, "UNRESOLVED", 0.0, "method not found on class or known bases", text, line))
                return
            self._name_only(f, src, method, line, text)  # self.attr.method(): attribute type unknown
            return
        # module alias chains, e.g. `util.helper`, `pkg.sub.func`, `ns.Class.method`
        for cut in range(len(parts) - 1, 0, -1):
            prefix = ".".join(parts[:cut])
            mb = mods.get(prefix)
            if mb is not None:
                rest = parts[cut:]
                if mb.kind == "external":
                    self._add(_Edge("CALLS", f.file_id, src, None, None, "UNRESOLVED", 0.0, "call into external module", text, line))
                    return
                self._call_in_module(f, src, mb, rest, line, text)
                return
        b = names.get(head)
        if b is not None and b.kind == "symbol" and b.symbol_id is not None and self.sym[b.symbol_id].kind == "class" and len(parts) == 2:
            cls = self.sym[b.symbol_id]
            sid, direct = self._method_in_hierarchy(self.files[cls.file_id], cls, method)
            if sid is not None:
                t = self.sym[sid]
                self._add(_Edge("CALLS", f.file_id, src, t.file_id, sid, "CONFIRMED", 0.9 * b.confidence, "class attribute via import binding", None, line))
                return
        if b is not None and b.kind == "external":
            self._add(_Edge("CALLS", f.file_id, src, None, None, "UNRESOLVED", 0.0, "call on externally imported name", text, line))
            return
        local = self._lexical_lookup(f, scope, head)
        if local is not None and self.sym[local].kind == "class" and len(parts) == 2:
            cls = self.sym[local]
            sid, direct = self._method_in_hierarchy(f, cls, method)
            if sid is not None:
                t = self.sym[sid]
                self._add(_Edge("CALLS", f.file_id, src, t.file_id, sid, "CONFIRMED", 0.9, "class attribute in same file", None, line))
                return
        if f.language == "python" and head in PY_BUILTINS:
            return
        # receiver is a local variable/parameter of unknown type -> name-only heuristic, clearly labelled
        self._name_only(f, src, method, line, text)

    def _call_binding(self, f, src, b: Binding, line, text) -> None:
        if b.kind == "symbol" and b.symbol_id is not None:
            self._add(_Edge("CALLS", f.file_id, src, b.file_id, b.symbol_id,
                            "CONFIRMED" if b.confidence >= 0.9 else "INFERRED", b.confidence, b.provenance, None, line))
        elif b.kind == "external":
            self._add(_Edge("CALLS", f.file_id, src, None, None, "UNRESOLVED", 0.0, "call to externally imported name", text, line))
        else:
            self._add(_Edge("CALLS", f.file_id, src, None, None, "UNRESOLVED", 0.0, b.provenance or "imported name not found in module", text, line))

    def _call_in_module(self, f, src, mb: Binding, rest: list[str], line, text) -> None:
        assert mb.file_id is not None
        tf = self.files[mb.file_id]
        sid = self.top_level.get(tf.file_id, {}).get(rest[0])
        if sid is None:
            names, _ = self._file_bindings(tf)
            rb = names.get(rest[0])
            if rb is not None and rb.kind == "symbol" and rb.symbol_id is not None:
                sid = rb.symbol_id
        if sid is None:
            self._add(_Edge("CALLS", f.file_id, src, None, None, "UNRESOLVED", 0.0, f"'{rest[0]}' not defined in module {tf.path}", text, line))
            return
        target = self.sym[sid]
        if len(rest) == 2 and target.kind == "class":
            msid, direct = self._method_in_hierarchy(self.files[target.file_id], target, rest[1])
            if msid is not None:
                target = self.sym[msid]
                sid = msid
        elif len(rest) > 1:
            self._add(_Edge("CALLS", f.file_id, src, None, None, "UNRESOLVED", 0.0, "deep attribute chain on module member", text, line))
            return
        self._add(_Edge("CALLS", f.file_id, src, target.file_id, sid, "CONFIRMED", 0.95 * mb.confidence, "module attribute via import", None, line))

    def _resolve_reference(self, f, src, scope, name, line, names) -> None:
        sid = self._lexical_lookup(f, scope, name)
        target_id, conf, why = None, 0.0, ""
        if sid is not None:
            target_id, conf, why = sid, 1.0, "same-file name reference"
        else:
            b = names.get(name)
            if b is not None and b.kind == "symbol" and b.symbol_id is not None:
                target_id, conf, why = b.symbol_id, b.confidence, "imported name reference"
        if target_id is None or target_id == src:
            return
        if (("CALLS", f.file_id, src, self.sym[target_id].file_id, target_id, None)) in self.edges:
            return  # already represented as a call
        t = self.sym[target_id]
        if t.kind == "module":
            return
        self._add(_Edge("REFERENCES", f.file_id, src, t.file_id, target_id, "CONFIRMED" if conf >= 0.9 else "INFERRED", conf, why, None, line))

    # ------------------------------------------------------------------ derived relations
    def _derive_tests(self) -> None:
        test_files = {f.file_id for f in self.files.values() if f.is_test}
        for e in list(self.edges.values()):
            if e.edge_type == "CALLS" and e.dst_symbol_id is not None and e.src_symbol_id is not None:
                s = self.sym[e.src_symbol_id]
                if s.is_test and e.dst_file_id not in test_files and e.resolution != "UNRESOLVED":
                    conf = min(0.8, e.confidence)
                    self._add(_Edge("TESTS", e.src_file_id, e.src_symbol_id, e.dst_file_id, e.dst_symbol_id, "INFERRED", conf,
                                    "test directly calls target", None, e.line))
            if e.edge_type == "IMPORTS" and e.src_file_id in test_files and e.dst_file_id is not None and e.dst_symbol_id is None \
                    and e.dst_file_id not in test_files and e.resolution == "CONFIRMED":
                self._add(_Edge("TESTS", e.src_file_id, None, e.dst_file_id, None, "INFERRED", 0.7, "test file imports module", None, e.line))
        # naming convention: test_foo.py / foo.test.ts / foo_test.py -> foo.*
        stems: dict[str, list[FileInfo]] = {}
        for f in self.files.values():
            if not f.is_test and f.language in ("python", "javascript", "typescript"):
                stems.setdefault(posixpath.splitext(posixpath.basename(f.path))[0], []).append(f)
        for tf in (self.files[i] for i in test_files):
            base = posixpath.basename(tf.path)
            stem = base.split(".")[0]
            stem = stem.removeprefix("test_").removesuffix("_test")
            cands = stems.get(stem, [])
            if len(cands) == 1:
                self._add(_Edge("TESTS", tf.file_id, None, cands[0].file_id, None, "INFERRED", 0.5, "naming convention", None, None))

    def _derive_depends_on(self) -> None:
        agg: dict[tuple[int, int], int] = {}
        for e in self.edges.values():
            if e.edge_type in ("CALLS", "INHERITS", "REFERENCES") and e.resolution != "UNRESOLVED" and e.src_file_id and e.dst_file_id \
                    and e.src_file_id != e.dst_file_id and e.confidence >= 0.5:
                agg[(e.src_file_id, e.dst_file_id)] = agg.get((e.src_file_id, e.dst_file_id), 0) + 1
        for (a, b), n in agg.items():
            self._add(_Edge("DEPENDS_ON", a, None, b, None, "CONFIRMED", 1.0, f"aggregated from {n} resolved symbol-level edge(s)"))
