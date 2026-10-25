"""Python source analysis using the standard-library `ast` module."""
from __future__ import annotations

import ast
import re

from app.parsing.ir import ParsedFile, ParsedImport, ParsedReference, ParsedSymbol

_TEST_FILE_RE = re.compile(r"(^|/)(test_[^/]*\.py|[^/]*_test\.py|conftest\.py)$|(^|/)tests?/")


def is_test_path(path: str) -> bool:
    return bool(_TEST_FILE_RE.search(path))


def _attr_chain(node: ast.AST) -> str | None:
    """Return dotted text for Name/Attribute chains, or None if the expression is dynamic (calls, subscripts...)."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    try:
        args = ast.unparse(node.args)
    except Exception:  # pragma: no cover - defensive
        args = "..."
    ret = ""
    if node.returns is not None:
        try:
            ret = " -> " + ast.unparse(node.returns)
        except Exception:  # pragma: no cover
            ret = ""
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({args}){ret}"


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: str, parsed: ParsedFile):
        self.path = path
        self.parsed = parsed
        self.scope: list[tuple[str, str]] = []  # (kind, qname)
        self.file_is_test = parsed.is_test

    # ---- helpers
    @property
    def current_qname(self) -> str:
        return self.scope[-1][1] if self.scope else ""

    def _qname(self, name: str) -> str:
        return f"{self.current_qname}.{name}" if self.current_qname else name

    def _add_symbol(self, node, kind: str, name: str, signature: str, bases: list[str] | None = None) -> ParsedSymbol:
        qname = self._qname(name)
        is_test = False
        if kind in ("function", "method"):
            is_test = name.startswith("test") and (self.file_is_test or self._in_test_class())
        if kind == "class":
            is_test = name.startswith("Test") and self.file_is_test
        end = getattr(node, "end_lineno", None) or node.lineno
        start = node.lineno
        if getattr(node, "decorator_list", None):
            start = min(start, *(d.lineno for d in node.decorator_list))
        sym = ParsedSymbol(
            kind=kind,
            name=name,
            qualified_name=qname,
            start_line=start,
            end_line=end,
            signature=signature,
            docstring=(ast.get_docstring(node) or "")[:2000],
            parent_qname=self.current_qname or None,
            is_test=is_test,
            bases=bases or [],
        )
        self.parsed.symbols.append(sym)
        return sym

    def _in_test_class(self) -> bool:
        return any(kind == "class" and q.split(".")[-1].startswith("Test") for kind, q in self.scope)

    # ---- visitors
    def visit_ClassDef(self, node: ast.ClassDef):
        bases = [t for b in node.bases if (t := _attr_chain(b))]
        self._add_symbol(node, "class", node.name, f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}", bases)
        self.scope.append(("class", self._qname(node.name)))
        for dec in node.decorator_list:
            self.visit(dec)
        for child in node.body:
            self.visit(child)
        self.scope.pop()

    def _visit_func(self, node):
        in_class = bool(self.scope) and self.scope[-1][0] == "class"
        kind = "method" if in_class else "function"
        self._add_symbol(node, kind, node.name, _signature(node))
        self.scope.append((kind, self._qname(node.name)))
        for dec in node.decorator_list:
            self.visit(dec)
        for child in node.body:
            self.visit(child)
        self.scope.pop()

    visit_FunctionDef = _visit_func
    visit_AsyncFunctionDef = _visit_func

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            # `import a.b.c` binds the name `a`; `import a.b as x` binds `x` to a.b.
            local = alias.asname or alias.name.split(".")[0]
            self.parsed.imports.append(
                ParsedImport(module=alias.name, names=[], line=node.lineno, kind="import", namespace_alias=local)
            )

    def visit_ImportFrom(self, node: ast.ImportFrom):
        self.parsed.imports.append(
            ParsedImport(
                module=node.module or "",
                names=[(a.name, a.asname or a.name) for a in node.names],
                level=node.level or 0,
                line=node.lineno,
                kind="from",
            )
        )

    def visit_Call(self, node: ast.Call):
        text = _attr_chain(node.func)
        if text:
            receiver = text.rsplit(".", 1)[0] if "." in text else None
            self.parsed.references.append(
                ParsedReference(kind="call", source_qname=self.current_qname, target_text=text, line=node.lineno, receiver=receiver)
            )
        elif isinstance(node.func, ast.Attribute):
            # Receiver is a dynamic expression (call result, subscript, ...): record it as name-only evidence.
            self.parsed.references.append(
                ParsedReference(kind="call", source_qname=self.current_qname, target_text=f"<dynamic>.{node.func.attr}", line=node.lineno)
            )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name):
        if isinstance(node.ctx, ast.Load):
            self.parsed.references.append(
                ParsedReference(kind="reference", source_qname=self.current_qname, target_text=node.id, line=node.lineno)
            )


def parse_python(path: str, source: str) -> ParsedFile:
    parsed = ParsedFile(language="python", is_test=is_test_path(path), line_count=source.count("\n") + (0 if source.endswith("\n") or not source else 1))
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        parsed.parse_error = f"SyntaxError: {e.msg} (line {e.lineno})"
        return parsed
    except (ValueError, RecursionError) as e:  # null bytes, extremely deep nesting
        parsed.parse_error = f"{type(e).__name__}: {e}"
        return parsed

    module_name = path.rsplit("/", 1)[-1].removesuffix(".py")
    parsed.symbols.append(
        ParsedSymbol(
            kind="module",
            name=module_name,
            qualified_name="<module>",
            start_line=1,
            end_line=max(parsed.line_count, 1),
            signature=f"module {module_name}",
            docstring=(ast.get_docstring(tree) or "")[:2000],
            is_test=parsed.is_test,
        )
    )
    _Visitor(path, parsed).visit(tree)
    return parsed
