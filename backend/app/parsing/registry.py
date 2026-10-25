"""Language detection and parser dispatch."""
from __future__ import annotations

from pathlib import PurePosixPath

from app.parsing.ir import ParsedFile
from app.parsing.js_parser import is_js_test_path, parse_js_like
from app.parsing.python_parser import is_test_path, parse_python

EXTENSION_LANGUAGE = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
}
# Files that are indexed as plain text / config nodes (searchable, no symbols).
CONFIG_NAMES = {"package.json", "pyproject.toml", "setup.cfg", "setup.py", "pytest.ini", "tox.ini", "requirements.txt", "tsconfig.json", "Makefile", "Dockerfile"}
CONFIG_EXTENSIONS = {".toml", ".ini", ".cfg", ".yaml", ".yml", ".json"}
DOC_EXTENSIONS = {".md", ".rst", ".txt"}


def detect_language(path: str) -> str:
    p = PurePosixPath(path)
    lang = EXTENSION_LANGUAGE.get(p.suffix.lower())
    if lang:
        return lang
    if p.name in CONFIG_NAMES or p.suffix.lower() in CONFIG_EXTENSIONS:
        return "config"
    if p.suffix.lower() in DOC_EXTENSIONS:
        return "docs"
    return "unknown"


def is_test_file(path: str, language: str) -> bool:
    if language == "python":
        return is_test_path(path)
    if language in ("javascript", "typescript"):
        return is_js_test_path(path)
    return False


def parse_source(path: str, language: str, source: str) -> ParsedFile:
    if language == "python":
        return parse_python(path, source)
    if language in ("javascript", "typescript"):
        return parse_js_like(path, source)
    return ParsedFile(language=language, line_count=source.count("\n") + 1 if source else 0)
