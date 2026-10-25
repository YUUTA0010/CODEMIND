"""JavaScript / TypeScript analysis using Tree-sitter.

Limits (documented honestly): no type-driven resolution, no dynamic `import()` resolution beyond string literals,
no CommonJS `module.exports` aliasing analysis. Calls on receivers of unknown type are reported as name-only
references and later labelled INFERRED/UNRESOLVED by the resolver.
"""
from __future__ import annotations

import re

from app.parsing.ir import ParsedFile, ParsedImport, ParsedReference, ParsedSymbol

try:  # Tree-sitter is optional: without it JS/TS files are indexed as plain text (no symbols).
    import tree_sitter_javascript as _tsj
    import tree_sitter_typescript as _tst
    from tree_sitter import Language, Parser

    _LANGS = {
        "javascript": Language(_tsj.language()),
        "typescript": Language(_tst.language_typescript()),
        "tsx": Language(_tst.language_tsx()),
    }
    TREE_SITTER_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only when wheels are missing
    _LANGS = {}
    TREE_SITTER_AVAILABLE = False

_TEST_FILE_RE = re.compile(r"(\.test\.|\.spec\.|(^|/)__tests__/)")
_TEST_CALLS = {"describe", "it", "test", "suite"}
_FUNC_VALUE_TYPES = {"arrow_function", "function_expression", "function", "generator_function"}


def is_js_test_path(path: str) -> bool:
    return bool(_TEST_FILE_RE.search(path))


def _grammar_for(path: str) -> str:
    if path.endswith(".tsx"):
        return "tsx"
    if path.endswith((".ts", ".mts", ".cts")):
        return "typescript"
    return "javascript"


def _text(node) -> str:
    return node.text.decode("utf-8", errors="replace") if node is not None else ""


def _string_value(node) -> str | None:
    if node is None or node.type not in ("string", "template_string"):
        return None
    raw = _text(node)
    if len(raw) >= 2 and raw[0] in "'\"`" and raw[-1] == raw[0]:
        return raw[1:-1]
    return None


def _member_chain(node) -> str | None:
    """Dotted text for identifier / this / member_expression chains; None for dynamic receivers."""
    if node.type in ("identifier", "property_identifier", "shorthand_property_identifier"):
        return _text(node)
    if node.type == "this":
        return "this"
    if node.type == "member_expression":
        obj = node.child_by_field_name("object")
        prop = node.child_by_field_name("property")
        base = _member_chain(obj) if obj is not None else None
        if base is None or prop is None:
            return None
        return f"{base}.{_text(prop)}"
    return None


class _Walker:
    def __init__(self, parsed: ParsedFile, file_is_test: bool):
        self.parsed = parsed
        self.file_is_test = file_is_test
        self.scope: list[tuple[str, str]] = []

    @property
    def qname(self) -> str:
        return self.scope[-1][1] if self.scope else ""

    def _q(self, name: str) -> str:
        return f"{self.qname}.{name}" if self.qname else name

    def add_symbol(self, node, kind: str, name: str, signature: str, *, is_test=False, bases=None, doc="") -> ParsedSymbol:
        sym = ParsedSymbol(
            kind=kind,
            name=name,
            qualified_name=self._q(name),
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            signature=signature[:300],
            docstring=doc,
            parent_qname=self.qname or None,
            is_test=is_test,
            bases=bases or [],
        )
        self.parsed.symbols.append(sym)
        return sym

    @staticmethod
    def _sig_until_body(node) -> str:
        body = node.child_by_field_name("body")
        src = _text(node)
        if body is not None:
            src = src[: body.start_byte - node.start_byte]
        return " ".join(src.split()).rstrip("=> ").strip()

    # ------------------------------------------------------------------ main dispatch
    def walk(self, node):
        t = node.type
        if t == "import_statement":
            self._import(node)
            return
        if t == "export_statement":
            src = _string_value(node.child_by_field_name("source"))
            if src is not None:
                self.parsed.imports.append(ParsedImport(module=src, line=node.start_point[0] + 1, kind="reexport", names=[("*", "*")]))
            for c in node.children:
                self.walk(c)
            return
        if t in ("function_declaration", "generator_function_declaration"):
            name = _text(node.child_by_field_name("name"))
            self.add_symbol(node, "method" if self._in_class() else "function", name, self._sig_until_body(node))
            self._descend(node, "function", name)
            return
        if t in ("class_declaration", "abstract_class_declaration"):
            self._class(node)
            return
        if t == "method_definition":
            name = _text(node.child_by_field_name("name"))
            self.add_symbol(node, "method", name, self._sig_until_body(node))
            self._descend(node, "method", name)
            return
        if t in ("interface_declaration", "type_alias_declaration"):
            nm = node.child_by_field_name("name")
            if nm is not None:
                self.add_symbol(node, "interface" if t == "interface_declaration" else "type", _text(nm), self._sig_until_body(node)[:200])
            return
        if t in ("lexical_declaration", "variable_declaration"):
            for decl in node.named_children:
                if decl.type == "variable_declarator":
                    self._declarator(decl, node)
            return
        if t == "call_expression":
            self._call(node)
            return
        if t == "new_expression":
            ctor = node.child_by_field_name("constructor")
            text = _member_chain(ctor) if ctor is not None else None
            if text:
                self.parsed.references.append(
                    ParsedReference("call", self.qname, text, node.start_point[0] + 1, text.rsplit(".", 1)[0] if "." in text else None)
                )
        for c in node.children:
            self.walk(c)

    def _in_class(self) -> bool:
        return bool(self.scope) and self.scope[-1][0] == "class"

    def _descend(self, node, kind: str, name: str):
        self.scope.append((kind, self._q(name)))
        for c in node.children:
            self.walk(c)
        self.scope.pop()

    def _class(self, node):
        name = _text(node.child_by_field_name("name"))
        bases: list[str] = []
        for c in node.children:
            if c.type == "class_heritage":
                for hc in c.children:
                    if hc.type == "extends_clause":
                        for v in hc.named_children:
                            chain = _member_chain(v)
                            if chain:
                                bases.append(chain)
                    elif hc.type in ("identifier", "member_expression"):
                        chain = _member_chain(hc)
                        if chain:
                            bases.append(chain)
        sig = f"class {name}" + (f" extends {', '.join(bases)}" if bases else "")
        self.add_symbol(node, "class", name, sig, bases=bases, is_test=False)
        self._descend(node, "class", name)

    def _declarator(self, decl, parent):
        name_node = decl.child_by_field_name("name")
        value = decl.child_by_field_name("value")
        if name_node is not None and name_node.type == "identifier" and value is not None:
            name = _text(name_node)
            if value.type in _FUNC_VALUE_TYPES:
                sig = f"{'export ' if parent.parent is not None and parent.parent.type == 'export_statement' else ''}const {name} = {self._sig_until_body(value)}"
                self.add_symbol(decl, "function", name, sig)
                self._descend(value, "function", name)
                return
            req = self._require_source(value)
            if req is not None:
                self.parsed.imports.append(
                    ParsedImport(module=req, line=decl.start_point[0] + 1, kind="require", namespace_alias=name)
                )
                return
        if value is not None:
            self.walk(value)

    @staticmethod
    def _require_source(node) -> str | None:
        if node.type == "call_expression":
            fn = node.child_by_field_name("function")
            args = node.child_by_field_name("arguments")
            if fn is not None and _text(fn) == "require" and args is not None and args.named_child_count >= 1:
                return _string_value(args.named_children[0])
        return None

    def _call(self, node):
        fn = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        line = node.start_point[0] + 1
        if fn is not None and fn.type == "identifier" and _text(fn) in _TEST_CALLS and args is not None and args.named_child_count >= 2:
            title = _string_value(args.named_children[0])
            cb = args.named_children[1]
            if title is not None and cb.type in _FUNC_VALUE_TYPES:
                self.add_symbol(node, "function", title, f"{_text(fn)}('{title}')", is_test=True)
                self.scope.append(("function", self._q(title)))
                for c in cb.children:
                    self.walk(c)
                self.scope.pop()
                return
        if fn is not None:
            if fn.type == "import":  # dynamic import("x")
                src = _string_value(args.named_children[0]) if args is not None and args.named_child_count else None
                if src:
                    self.parsed.imports.append(ParsedImport(module=src, line=line, kind="import", namespace_alias=None))
            else:
                text = _member_chain(fn)
                if text:
                    self.parsed.references.append(
                        ParsedReference("call", self.qname, text, line, text.rsplit(".", 1)[0] if "." in text else None)
                    )
                elif fn.type == "member_expression":
                    prop = fn.child_by_field_name("property")
                    if prop is not None:
                        self.parsed.references.append(ParsedReference("call", self.qname, f"<dynamic>.{_text(prop)}", line, None))
        for c in node.children:
            self.walk(c)

    def _import(self, node):
        src = _string_value(node.child_by_field_name("source"))
        if src is None:
            return
        imp = ParsedImport(module=src, line=node.start_point[0] + 1, kind="from")
        for c in node.children:
            if c.type == "import_clause":
                for cc in c.children:
                    if cc.type == "identifier":  # default import
                        imp.names.append(("default", _text(cc)))
                    elif cc.type == "namespace_import":
                        ident = next((x for x in cc.children if x.type == "identifier"), None)
                        imp.namespace_alias = _text(ident) if ident is not None else None
                    elif cc.type == "named_imports":
                        for spec in cc.named_children:
                            if spec.type == "import_specifier":
                                nm = spec.child_by_field_name("name")
                                al = spec.child_by_field_name("alias")
                                imp.names.append((_text(nm), _text(al) if al is not None else _text(nm)))
        self.parsed.imports.append(imp)


def parse_js_like(path: str, source: str) -> ParsedFile:
    lang_key = _grammar_for(path)
    language = "typescript" if lang_key in ("typescript", "tsx") else "javascript"
    parsed = ParsedFile(language=language, is_test=is_js_test_path(path), line_count=source.count("\n") + (0 if source.endswith("\n") or not source else 1))
    if not TREE_SITTER_AVAILABLE:
        parsed.parse_error = "tree-sitter grammars not installed; file indexed as text only"
        return parsed
    try:
        parser = Parser(_LANGS[lang_key])
        tree = parser.parse(source.encode("utf-8", errors="replace"))
    except Exception as e:  # pragma: no cover - defensive
        parsed.parse_error = f"{type(e).__name__}: {e}"
        return parsed

    module_name = re.sub(r"\.(tsx?|jsx?|mjs|cjs|mts|cts)$", "", path.rsplit("/", 1)[-1])
    parsed.symbols.append(
        ParsedSymbol(kind="module", name=module_name, qualified_name="<module>", start_line=1, end_line=max(parsed.line_count, 1),
                     signature=f"module {module_name}", is_test=parsed.is_test)
    )
    walker = _Walker(parsed, parsed.is_test)
    try:
        walker.walk(tree.root_node)
    except RecursionError:  # pragma: no cover - pathological nesting
        parsed.parse_error = "RecursionError: syntax tree too deep"
        return parsed
    if tree.root_node.has_error:
        # Tree-sitter recovers from errors, so symbols may still be useful; record that the file had syntax errors.
        parsed.parse_error = "syntax errors present (partial results)"
    # Mark methods/functions in test files that look like tests (jest-style callbacks already flagged).
    if parsed.is_test:
        for s in parsed.symbols:
            if s.kind in ("function", "method") and (s.name.startswith("test") or s.is_test):
                s.is_test = True
    return parsed
