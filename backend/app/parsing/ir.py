"""Language-neutral intermediate representation (IR) produced by every parser.

Parsers only report what they can see syntactically. Resolving a reference to a concrete target is the job of
`app.graph.resolver`, which labels every edge CONFIRMED / INFERRED / UNRESOLVED.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParsedSymbol:
    kind: str  # module | class | function | method | interface | type
    name: str
    qualified_name: str
    start_line: int
    end_line: int
    signature: str = ""
    docstring: str = ""
    parent_qname: str | None = None
    is_test: bool = False
    bases: list[str] = field(default_factory=list)  # textual base-class expressions


@dataclass
class ParsedImport:
    module: str  # raw module text, e.g. "pkg.sub" or "./util"
    names: list[tuple[str, str]] = field(default_factory=list)  # (imported_name, local_alias); "*" for star imports
    level: int = 0  # python relative import level
    line: int = 0
    kind: str = "import"  # import | from | require | reexport
    namespace_alias: str | None = None  # `import x as y` / `import * as y` / `const y = require()`


@dataclass
class ParsedReference:
    kind: str  # call | reference
    source_qname: str  # qualified name of the enclosing symbol ("" => module level)
    target_text: str  # e.g. "foo", "self.bar", "mod.func", "obj.method"
    line: int
    receiver: str | None = None  # text before the final attribute, if any


@dataclass
class ParsedFile:
    language: str
    symbols: list[ParsedSymbol] = field(default_factory=list)
    imports: list[ParsedImport] = field(default_factory=list)
    references: list[ParsedReference] = field(default_factory=list)
    parse_error: str | None = None
    is_test: bool = False
    line_count: int = 0
