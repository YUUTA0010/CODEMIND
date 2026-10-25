"""Source parsing, symbol extraction, imports, calls, test detection and parse-error handling."""
from app.parsing.registry import detect_language, is_test_file, parse_source

PY = '''"""mod doc"""
import os
import a.b.c
from .util import helper as h, other
from pkg import *


class Base:
    pass


@decorator
class A(Base, mixins.M):
    """A doc"""

    def m(self, x: int, *, y=2) -> int:
        """m doc"""
        return h(x) + self.n()

    async def n(self):
        return os.getcwd()


def top(a, b=1):
    def inner():
        return a
    return inner()


def test_top():
    assert top(1)
'''


def sym(parsed, qname):
    return next(s for s in parsed.symbols if s.qualified_name == qname)


def test_python_symbols_ranges_and_signatures():
    p = parse_source("pkg/mod.py", "python", PY)
    assert p.parse_error is None
    kinds = {s.qualified_name: s.kind for s in p.symbols}
    assert kinds["A"] == "class" and kinds["A.m"] == "method" and kinds["A.n"] == "method"
    assert kinds["top"] == "function" and kinds["top.inner"] == "function"
    m = sym(p, "A.m")
    assert m.signature == "def m(self, x: int, *, y=2) -> int"
    assert (m.start_line, m.end_line) == (16, 18)  # exact line range of the method
    assert sym(p, "A.n").signature.startswith("async def n")
    assert sym(p, "A").bases == ["Base", "mixins.M"]
    assert sym(p, "A.m").docstring == "m doc"
    assert sym(p, "A").start_line == 12  # `@decorator` is on line 12; decorators are part of the symbol's range


def test_python_imports_and_calls():
    p = parse_source("pkg/mod.py", "python", PY)
    mods = {(i.module, i.level, tuple(i.names)) for i in p.imports}
    assert ("os", 0, ()) in mods
    assert ("util", 1, (("helper", "h"), ("other", "other"))) in mods
    assert ("pkg", 0, (("*", "*"),)) in mods
    calls = {(r.source_qname, r.target_text) for r in p.references if r.kind == "call"}
    assert ("A.m", "h") in calls and ("A.m", "self.n") in calls and ("A.n", "os.getcwd") in calls
    assert ("top", "inner") in calls


def test_python_dynamic_receiver_is_recorded_as_dynamic():
    p = parse_source("x.py", "python", "def f(x):\n    return make().run()\n")
    assert any(r.target_text == "<dynamic>.run" for r in p.references)


def test_python_test_detection():
    p = parse_source("tests/test_a.py", "python", PY)
    assert p.is_test and sym(p, "test_top").is_test and not sym(p, "A.m").is_test
    assert is_test_file("tests/test_a.py", "python") and is_test_file("src/x_test.py", "python")
    assert not is_test_file("src/app.py", "python")


def test_python_syntax_error_is_reported_not_raised():
    p = parse_source("bad.py", "python", "def (:\n  pass\n")
    assert p.parse_error and "SyntaxError" in p.parse_error and p.symbols == []


def test_python_null_bytes_do_not_crash():
    p = parse_source("nul.py", "python", "x = 1\x00\n")
    assert p.parse_error


JS = '''import {a as b, c} from './x';
import d from "./d";
import * as ns from './ns';
const e = require('./e');
export * from './reexp';

export class K extends Base {
  go() { this.run(); b(1); }
}
export const f = (x) => { return c(x) + e.thing(); };
function plain(a) { return a; }
describe('suite', () => { it('works', () => { f(1); }); });
'''


def test_js_symbols_imports_calls():
    p = parse_source("src/k.test.ts", "typescript", JS)
    assert p.parse_error is None and p.is_test
    names = {s.qualified_name: s for s in p.symbols}
    assert names["K"].kind == "class" and names["K"].bases == ["Base"]
    assert names["K.go"].kind == "method"
    assert names["f"].kind == "function" and names["plain"].kind == "function"
    assert names["suite.works"].is_test
    imps = {(i.module, i.kind) for i in p.imports}
    assert {("./x", "from"), ("./d", "from"), ("./ns", "from"), ("./e", "require"), ("./reexp", "reexport")} <= imps
    named = next(i for i in p.imports if i.module == "./x")
    assert named.names == [("a", "b"), ("c", "c")]
    assert next(i for i in p.imports if i.module == "./ns").namespace_alias == "ns"
    calls = {(r.source_qname, r.target_text) for r in p.references if r.kind == "call"}
    assert ("K.go", "this.run") in calls and ("f", "e.thing") in calls


def test_ts_interface_and_syntax_error_partial():
    p = parse_source("a.ts", "typescript", "export interface I { a: number }\nexport function ok(){ return 1 }\nfunction broken( {")
    assert any(s.kind == "interface" and s.name == "I" for s in p.symbols)
    assert p.parse_error and "syntax errors" in p.parse_error


def test_language_detection():
    assert detect_language("a/b.py") == "python"
    assert detect_language("a/b.tsx") == "typescript"
    assert detect_language("a/b.mjs") == "javascript"
    assert detect_language("package.json") == "config"
    assert detect_language("README.md") == "docs"
    assert detect_language("image.png") == "unknown"
