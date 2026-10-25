"""Repository ingestion: root detection, ignore rules, file discovery, hashing.

Read-only with respect to the repository. Never follows symlinks out of the repository, never runs `git`
(git can execute repository-configured programs such as core.fsmonitor), and treats all content as untrusted data.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import pathspec

from app.core.errors import AppError
from app.parsing.registry import detect_language

DEFAULT_IGNORE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", ".env", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".nox", "dist", "build", "target", "out", ".next", ".nuxt", "coverage", "htmlcov", "site-packages",
    ".idea", ".vscode", ".gradle", ".cache", "bower_components", ".eggs", "*.egg-info",
}
DEFAULT_IGNORE_FILE_SUFFIXES = (".min.js", ".min.css", ".map", ".lock", ".pyc", ".pyo", ".so", ".dll", ".exe", ".class", ".jar")
DEFAULT_IGNORE_FILE_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock"}
INDEXED_LANGUAGES = {"python", "javascript", "typescript", "config", "docs"}
CODEMIND_IGNORE_FILE = ".codemindignore"


@dataclass
class FileCandidate:
    path: str  # POSIX relative path
    abs_path: Path
    size: int
    mtime_ns: int
    language: str


@dataclass
class WalkStats:
    seen: int = 0
    candidates: int = 0
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def detect_repository_root(path: str | Path) -> tuple[Path, bool]:
    """Return (root, is_git). Validates that the path is an existing directory and not a filesystem root."""
    p = Path(path).expanduser()
    try:
        p = p.resolve(strict=True)
    except (FileNotFoundError, OSError) as e:
        raise AppError(f"Path does not exist or is unreadable: {path}", code="invalid_path") from e
    if not p.is_dir():
        raise AppError(f"Path is not a directory: {path}", code="invalid_path")
    if p == Path(p.anchor) or p == Path.home().resolve():
        raise AppError("Refusing to index a filesystem root or home directory; choose a project folder.", code="invalid_path")
    for candidate in [p, *p.parents]:
        if (candidate / ".git").exists():
            return candidate, True
        if candidate == Path(candidate.anchor):
            break
    return p, False


def read_git_head(root: Path) -> str | None:
    """Read HEAD commit by parsing .git files directly (no git subprocess)."""
    git = root / ".git"
    try:
        if git.is_file():  # worktree/submodule pointer; do not follow
            return None
        head = (git / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
        if head.startswith("ref:"):
            ref = head.split(":", 1)[1].strip()
            ref_file = git / ref
            if ref_file.is_file():
                return ref_file.read_text().strip()[:64]
            packed = git / "packed-refs"
            if packed.is_file():
                for line in packed.read_text(errors="replace").splitlines():
                    if line.endswith(" " + ref):
                        return line.split()[0][:64]
            return None
        return head[:64]
    except OSError:
        return None


def _load_spec(path: Path) -> pathspec.GitIgnoreSpec | None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    return pathspec.GitIgnoreSpec.from_lines(lines)


class IgnoreMatcher:
    """Combines built-in defaults, nested .gitignore files and .codemindignore (root)."""

    def __init__(self, root: Path, extra_patterns: list[str] | None = None):
        self.root = root
        self._cache: dict[Path, pathspec.GitIgnoreSpec | None] = {}
        cm = _load_spec(root / CODEMIND_IGNORE_FILE)
        self.codemind = cm
        self.extra = pathspec.GitIgnoreSpec.from_lines(extra_patterns) if extra_patterns else None

    def _spec_for(self, directory: Path) -> pathspec.GitIgnoreSpec | None:
        if directory not in self._cache:
            self._cache[directory] = _load_spec(directory / ".gitignore")
        return self._cache[directory]

    def is_ignored(self, abs_path: Path, is_dir: bool) -> bool:
        rel = abs_path.relative_to(self.root).as_posix()
        name = abs_path.name
        if is_dir and (name in DEFAULT_IGNORE_DIRS or name.endswith(".egg-info")):
            return True
        if not is_dir and (name in DEFAULT_IGNORE_FILE_NAMES or name.endswith(DEFAULT_IGNORE_FILE_SUFFIXES)):
            return True
        probe = rel + "/" if is_dir else rel
        if self.codemind and self.codemind.match_file(probe):
            return True
        if self.extra and self.extra.match_file(probe):
            return True
        # nested .gitignore files from the root down to the file's directory
        parts = Path(rel).parts
        chain = [self.root] + [self.root.joinpath(*parts[: i + 1]) for i in range(len(parts) - 1)]
        for d in chain:
            spec = self._spec_for(d)
            if spec is None:
                continue
            sub = abs_path.relative_to(d).as_posix() + ("/" if is_dir else "")
            if spec.match_file(sub):
                return True
        return False


def is_binary_bytes(sample: bytes) -> bool:
    if b"\x00" in sample:
        return True
    if not sample:
        return False
    text_chars = bytes(range(32, 127)) + b"\n\r\t\f\b"
    nontext = sum(1 for b in sample if b < 128 and b not in text_chars)
    return nontext / len(sample) > 0.30


def walk_repository(root: Path, *, max_file_bytes: int, max_files: int, extra_ignore: list[str] | None = None,
                    stats: WalkStats | None = None) -> Iterator[FileCandidate]:
    stats = stats or WalkStats()
    matcher = IgnoreMatcher(root, extra_ignore)
    root_real = root.resolve()
    count = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        d = Path(dirpath)
        keep = []
        for name in sorted(dirnames):
            full = d / name
            if full.is_symlink():
                stats.skip("symlink_dir")
                continue
            if matcher.is_ignored(full, True):
                stats.skip("ignored_dir")
                continue
            keep.append(name)
        dirnames[:] = keep
        for name in sorted(filenames):
            full = d / name
            stats.seen += 1
            try:
                if full.is_symlink():
                    try:
                        target = full.resolve(strict=True)
                    except OSError:
                        stats.skip("broken_symlink")
                        continue
                    if root_real not in target.parents and target != root_real:
                        stats.skip("symlink_outside_repo")
                        continue
                if matcher.is_ignored(full, False):
                    stats.skip("ignored_file")
                    continue
                language = detect_language(full.relative_to(root).as_posix())
                if language not in INDEXED_LANGUAGES:
                    stats.skip("unsupported_type")
                    continue
                st = full.stat()
                if st.st_size > max_file_bytes:
                    stats.skip("too_large")
                    continue
                with open(full, "rb") as fh:
                    sample = fh.read(8192)
                if is_binary_bytes(sample):
                    stats.skip("binary")
                    continue
            except OSError:
                stats.skip("unreadable")
                continue
            count += 1
            if count > max_files:
                stats.skip("max_files_exceeded")
                return
            stats.candidates += 1
            yield FileCandidate(full.relative_to(root).as_posix(), full, st.st_size, st.st_mtime_ns, language)


def read_text(path: Path) -> tuple[str, str]:
    """Read file (bounded by caller) -> (text, sha256 of raw bytes). Invalid UTF-8 is replaced, never raises."""
    data = path.read_bytes()
    return data.decode("utf-8", errors="replace"), hashlib.sha256(data).hexdigest()


def compute_fingerprint(pairs: list[tuple[str, str]]) -> str:
    h = hashlib.sha256()
    for p, sha in sorted(pairs):
        h.update(f"{p}\0{sha}\n".encode())
    return h.hexdigest()
