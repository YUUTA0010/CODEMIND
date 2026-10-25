"""Unified-diff parsing, path policy and a strict in-process applier (no `patch`/`git apply` subprocess)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from app.core.errors import AppError, SecurityViolation

ALLOWED_EXTENSIONS = {".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".json", ".md", ".txt", ".toml", ".cfg", ".ini", ".yaml", ".yml", ".rst", ".css", ".html"}
FORBIDDEN_PARTS = {".git", ".github", ".ssh", ".env", "node_modules", ".venv", "__pycache__"}
FORBIDDEN_NAMES = {".gitignore", ".gitattributes", ".gitmodules", "Makefile", "Dockerfile", "setup.py", "conftest.py"}
MAX_DIFF_BYTES = 200_000
MAX_FILES = 20
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class PatchError(AppError):
    code = "invalid_patch"
    status_code = 422


@dataclass
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    lines: list[str] = field(default_factory=list)  # each prefixed with ' ', '+', '-'


@dataclass
class FilePatch:
    old_path: str | None
    new_path: str | None
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""

    @property
    def is_new(self) -> bool:
        return self.old_path is None

    @property
    def is_delete(self) -> bool:
        return self.new_path is None


def _strip_prefix(p: str) -> str | None:
    p = p.split("\t")[0].strip()
    if p == "/dev/null":
        return None
    if p.startswith(("a/", "b/")):
        p = p[2:]
    return p


def validate_rel_path(rel: str, *, allow_tests_write: bool = True) -> str:
    """Reject traversal, absolute paths, drive letters, forbidden locations and disallowed file types."""
    if not rel or "\x00" in rel or "\\" in rel:
        raise SecurityViolation(f"invalid path in patch: {rel!r}")
    if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        raise SecurityViolation(f"absolute path in patch rejected: {rel}")
    pp = PurePosixPath(rel)
    if ".." in pp.parts:
        raise SecurityViolation(f"path traversal rejected: {rel}")
    if any(part in FORBIDDEN_PARTS for part in pp.parts) or pp.name in FORBIDDEN_NAMES:
        raise SecurityViolation(f"patch touches a protected path: {rel}")
    if pp.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise SecurityViolation(f"file type not allowed in patches: {rel}")
    return str(pp)


def parse_unified_diff(diff: str) -> list[FilePatch]:
    if len(diff.encode("utf-8", errors="replace")) > MAX_DIFF_BYTES:
        raise PatchError("diff too large")
    if "\x00" in diff:
        raise PatchError("diff contains NUL bytes")
    if re.search(r"^(GIT binary patch|Binary files )", diff, re.M):
        raise PatchError("binary patches are not supported")
    if re.search(r"^(rename from|rename to|copy from|copy to|old mode|new mode|deleted file mode 120000|new file mode 120000)", diff, re.M):
        raise PatchError("renames, copies, mode changes and symlinks are not supported")
    lines = diff.replace("\r\n", "\n").split("\n")
    patches: list[FilePatch] = []
    i = 0
    cur: FilePatch | None = None
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            old, new = _strip_prefix(ln[4:]), _strip_prefix(lines[i + 1][4:])
            cur = FilePatch(old, new)
            patches.append(cur)
            i += 2
            continue
        m = _HUNK.match(ln)
        if m:
            if cur is None:
                raise PatchError("hunk before file header")
            h = Hunk(int(m.group(1)), int(m.group(2) or 1), int(m.group(3)), int(m.group(4) or 1))
            i += 1
            o = n = 0
            while i < len(lines) and (o < h.old_len or n < h.new_len):
                body = lines[i]
                if body.startswith("\\"):  # "\ No newline at end of file"
                    i += 1
                    continue
                if body == "" and i == len(lines) - 1:
                    break
                tag = body[:1]
                if tag == " " or body == "":
                    h.lines.append(" " + body[1:])
                    o += 1
                    n += 1
                elif tag == "-":
                    h.lines.append(body)
                    o += 1
                elif tag == "+":
                    h.lines.append(body)
                    n += 1
                else:
                    raise PatchError(f"malformed hunk line: {body[:60]!r}")
                i += 1
            if o != h.old_len or n != h.new_len:
                raise PatchError(f"hunk line counts do not match header (@@ -{h.old_start},{h.old_len} +{h.new_start},{h.new_len})")
            cur.hunks.append(h)
            continue
        i += 1
    if not patches:
        raise PatchError("no file changes found in diff")
    if len(patches) > MAX_FILES:
        raise PatchError(f"too many files in one patch (max {MAX_FILES})")
    seen = set()
    for fp in patches:
        if not fp.hunks:
            raise PatchError(f"no hunks for {fp.path}")
        for p in (fp.old_path, fp.new_path):
            if p:
                validate_rel_path(p)
        if fp.old_path and fp.new_path and fp.old_path != fp.new_path:
            raise PatchError("renames are not supported")
        if fp.path in seen:
            raise PatchError(f"file appears twice in diff: {fp.path}")
        seen.add(fp.path)
    return patches


def changed_files(diff: str) -> list[str]:
    return [fp.path for fp in parse_unified_diff(diff)]


def apply_hunks(original: list[str], fp: FilePatch) -> list[str]:
    """Apply hunks with exact context matching (no fuzz). Raises PatchError on any mismatch."""
    out: list[str] = []
    pos = 0  # index into original
    for h in fp.hunks:
        start = h.old_start - 1 if h.old_len else h.old_start
        if start < pos:
            raise PatchError(f"overlapping or out-of-order hunks in {fp.path}")
        out.extend(original[pos:start])
        pos = start
        for ln in h.lines:
            tag, text = ln[0], ln[1:]
            if tag in (" ", "-"):
                if pos >= len(original) or original[pos] != text:
                    got = original[pos] if pos < len(original) else "<EOF>"
                    raise PatchError(f"context mismatch in {fp.path} at line {pos + 1}: expected {text!r}, found {got!r}")
                if tag == " ":
                    out.append(text)
                pos += 1
            else:
                out.append(text)
    out.extend(original[pos:])
    return out


def _read_lines(p: Path) -> tuple[list[str], bool]:
    data = p.read_text(encoding="utf-8", errors="strict")
    trailing = data.endswith("\n")
    lines = data.split("\n")
    if trailing:
        lines.pop()
    return lines, trailing


def apply_to_directory(diff: str, root: Path, *, forbid_symlinks: bool = True) -> list[str]:
    """Apply a validated diff beneath `root`. All target paths are resolved and must stay inside `root`. Two-phase:
    compute every result in memory first, then write, so a failing hunk never leaves a half-applied tree."""
    root = root.resolve()
    patches = parse_unified_diff(diff)
    staged: list[tuple[Path, str | None]] = []
    for fp in patches:
        rel = validate_rel_path(fp.path)
        target = (root / rel).resolve()
        if root not in target.parents:
            raise SecurityViolation(f"patch target escapes sandbox: {rel}")
        if forbid_symlinks:
            cur = root
            for part in PurePosixPath(rel).parts:
                cur = cur / part
                if cur.is_symlink():
                    raise SecurityViolation(f"patch path passes through a symlink: {rel}")
        if fp.is_new:
            if target.exists():
                raise PatchError(f"file already exists: {rel}")
            new_lines = [ln[1:] for h in fp.hunks for ln in h.lines if ln[0] == "+"]
            staged.append((target, "\n".join(new_lines) + "\n"))
        elif fp.is_delete:
            if not target.is_file():
                raise PatchError(f"cannot delete missing file: {rel}")
            staged.append((target, None))
        else:
            if not target.is_file():
                raise PatchError(f"file to modify does not exist: {rel}")
            try:
                orig, trailing = _read_lines(target)
            except UnicodeDecodeError as e:
                raise PatchError(f"{rel} is not valid UTF-8 text") from e
            res = apply_hunks(orig, fp)
            staged.append((target, "\n".join(res) + ("\n" if trailing or not res else "")))
    for target, content in staged:
        if content is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".cm_tmp")
            tmp.write_text(content, encoding="utf-8")
            os.replace(tmp, target)
    return [str(t.relative_to(root)) for t, _ in staged]
