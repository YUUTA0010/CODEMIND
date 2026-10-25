import os
from pathlib import Path

import pytest

from app.core.errors import AppError
from app.indexing.ingest import (WalkStats, compute_fingerprint, detect_repository_root, is_binary_bytes, read_git_head, walk_repository)
from tests.conftest import write_tree


def walk(root, **kw):
    st = WalkStats()
    files = {c.path for c in walk_repository(root, max_file_bytes=kw.pop("max_file_bytes", 10_000), max_files=kw.pop("max_files", 1000), stats=st, **kw)}
    return files, st


def test_default_ignores_gitignore_and_codemindignore(tmp_path):
    write_tree(tmp_path, {
        "src/a.py": "x=1\n", "src/gen/out.py": "y=1\n", "node_modules/m/i.js": "1\n", ".venv/lib/x.py": "1\n", "build/b.py": "1\n", "__pycache__/c.pyc": "x",
        "notes.log": "x\n", "src/keep.py": "1\n", "docs/skip_me.md": "x\n", ".gitignore": "*.log\nsrc/gen/\n", ".codemindignore": "docs/\n", "yarn.lock": "x\n",
        "app.min.js": "1\n", "src/sub/.gitignore": "private.py\n", "src/sub/private.py": "1\n", "src/sub/public.py": "1\n",
    })
    files, st = walk(tmp_path)
    assert files == {"src/a.py", "src/keep.py", "src/sub/public.py"}
    assert "src/gen/out.py" not in files and "src/sub/private.py" not in files and "docs/skip_me.md" not in files
    assert not any(f.startswith(("node_modules", ".venv", "build")) for f in files)


def test_binary_oversize_and_unsupported(tmp_path):
    write_tree(tmp_path, {"ok.py": "x=1\n", "big.py": "x=1\n" * 5000, "pic.png": "x"})
    (tmp_path / "bin.py").write_bytes(b"\x00\x01\x02binary")
    files, st = walk(tmp_path, max_file_bytes=1000)
    assert files == {"ok.py"}
    assert st.skipped.get("too_large") == 1 and st.skipped.get("binary") == 1 and st.skipped.get("unsupported_type", 0) >= 1
    assert is_binary_bytes(b"a\x00b") and not is_binary_bytes(b"plain text\n")


def test_symlink_outside_repo_is_never_indexed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("SECRET=1\n")
    repo = tmp_path / "repo"
    write_tree(repo, {"a.py": "x=1\n"})
    os.symlink(outside / "secret.py", repo / "link.py")
    os.symlink(outside, repo / "linkdir")
    files, st = walk(repo)
    assert files == {"a.py"}
    assert st.skipped.get("symlink_outside_repo") == 1 and st.skipped.get("symlink_dir") == 1


def test_unreadable_file_is_skipped_gracefully(tmp_path):
    write_tree(tmp_path, {"a.py": "x=1\n", "locked.py": "y=1\n"})
    os.chmod(tmp_path / "locked.py", 0)
    try:
        if os.access(tmp_path / "locked.py", os.R_OK):
            pytest.skip("running as a user that ignores file modes (root)")
        files, st = walk(tmp_path)
        assert files == {"a.py"} and st.skipped.get("unreadable") == 1
    finally:
        os.chmod(tmp_path / "locked.py", 0o644)


def test_max_files_limit(tmp_path):
    write_tree(tmp_path, {f"f{i}.py": "x=1\n" for i in range(10)})
    files, st = walk(tmp_path, max_files=4)
    assert len(files) == 4 and st.skipped.get("max_files_exceeded") == 1


def test_root_detection_and_validation(tmp_path):
    (tmp_path / "proj" / ".git").mkdir(parents=True)
    (tmp_path / "proj" / "sub").mkdir()
    root, is_git = detect_repository_root(tmp_path / "proj" / "sub")
    assert root == (tmp_path / "proj").resolve() and is_git
    plain = tmp_path / "plain"
    plain.mkdir()
    assert detect_repository_root(plain) == (plain.resolve(), False)
    with pytest.raises(AppError):
        detect_repository_root(tmp_path / "missing")
    with pytest.raises(AppError):
        detect_repository_root("/")
    f = tmp_path / "file.txt"
    f.write_text("x")
    with pytest.raises(AppError):
        detect_repository_root(f)


def test_git_head_read_without_running_git(tmp_path):
    g = tmp_path / ".git"
    (g / "refs" / "heads").mkdir(parents=True)
    (g / "HEAD").write_text("ref: refs/heads/main\n")
    (g / "refs" / "heads" / "main").write_text("a" * 40 + "\n")
    assert read_git_head(tmp_path) == "a" * 40


def test_fingerprint_is_deterministic_and_content_sensitive():
    a = compute_fingerprint([("a.py", "1"), ("b.py", "2")])
    assert a == compute_fingerprint([("b.py", "2"), ("a.py", "1")])
    assert a != compute_fingerprint([("a.py", "1"), ("b.py", "3")])
    assert a != compute_fingerprint([("a.py", "1"), ("c.py", "2")])
