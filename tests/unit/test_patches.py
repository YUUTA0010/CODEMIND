"""Patch parsing, path policy and strict application."""
import pytest

from app.core.errors import AppError, SecurityViolation
from app.patches.diff import PatchError, apply_to_directory, changed_files, parse_unified_diff, validate_rel_path

ORIG = "a\nb\nc\nd\ne\n"
GOOD = "--- a/f.py\n+++ b/f.py\n@@ -2,3 +2,3 @@\n b\n-c\n+C\n d\n"


def mk(tmp_path, files):
    for rel, c in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(c)
    return tmp_path


def test_parse_and_apply_modification(tmp_path):
    mk(tmp_path, {"f.py": ORIG})
    fp = parse_unified_diff(GOOD)[0]
    assert fp.path == "f.py" and len(fp.hunks) == 1
    assert apply_to_directory(GOOD, tmp_path) == ["f.py"]
    assert (tmp_path / "f.py").read_text() == "a\nb\nC\nd\ne\n"


def test_context_mismatch_is_rejected_without_fuzz(tmp_path):
    mk(tmp_path, {"f.py": "a\nb\nX\nd\ne\n"})
    with pytest.raises(PatchError, match="context mismatch"):
        apply_to_directory(GOOD, tmp_path)
    assert (tmp_path / "f.py").read_text() == "a\nb\nX\nd\ne\n"


def test_no_partial_application_across_files(tmp_path):
    mk(tmp_path, {"f.py": ORIG, "g.py": "zzz\n"})
    diff = GOOD + "--- a/g.py\n+++ b/g.py\n@@ -1 +1 @@\n-nope\n+yes\n"
    with pytest.raises(PatchError):
        apply_to_directory(diff, tmp_path)
    assert (tmp_path / "f.py").read_text() == ORIG, "first file must stay untouched when a later file fails"


def test_new_file_and_delete(tmp_path):
    mk(tmp_path, {"old.py": "x\n"})
    diff = "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,2 @@\n+one\n+two\n--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"
    apply_to_directory(diff, tmp_path)
    assert (tmp_path / "new.py").read_text() == "one\ntwo\n" and not (tmp_path / "old.py").exists()


def test_hunk_header_count_mismatch_rejected():
    with pytest.raises(PatchError, match="line counts"):
        parse_unified_diff("--- a/f.py\n+++ b/f.py\n@@ -1,5 +1,5 @@\n a\n-b\n+B\n")


@pytest.mark.parametrize("bad", [
    "--- a/../../etc/passwd\n+++ b/../../etc/passwd\n@@ -1 +1 @@\n-x\n+y\n",
    "--- a//etc/x.py\n+++ b//etc/x.py\n@@ -1 +1 @@\n-x\n+y\n",
    "--- a/C:/win/x.py\n+++ b/C:/win/x.py\n@@ -1 +1 @@\n-x\n+y\n",
    "--- a/.git/hooks/pre-commit\n+++ b/.git/hooks/pre-commit\n@@ -1 +1 @@\n-x\n+y\n",
    "--- a/.github/workflows/ci.yml\n+++ b/.github/workflows/ci.yml\n@@ -1 +1 @@\n-x\n+y\n",
    "--- a/run.sh\n+++ b/run.sh\n@@ -1 +1 @@\n-x\n+y\n",
    "--- a/setup.py\n+++ b/setup.py\n@@ -1 +1 @@\n-x\n+y\n",
    "--- a/tests/conftest.py\n+++ b/tests/conftest.py\n@@ -1 +1 @@\n-x\n+y\n",
    "--- a/node_modules/x/i.js\n+++ b/node_modules/x/i.js\n@@ -1 +1 @@\n-x\n+y\n",
])
def test_dangerous_paths_rejected(bad):
    with pytest.raises(SecurityViolation):
        parse_unified_diff(bad)


@pytest.mark.parametrize("bad", [
    "diff --git a/x.py b/y.py\nrename from x.py\nrename to y.py\n",
    "GIT binary patch\nliteral 3\n",
    "--- a/f.py\n+++ b/f.py\nnew file mode 120000\n",
    "",
    "just some prose, no diff",
    "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n+y\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-y\n+z\n",
])
def test_unsupported_or_malformed_diffs_rejected(bad):
    with pytest.raises(AppError):
        parse_unified_diff(bad)


def test_oversized_diff_rejected():
    with pytest.raises(PatchError, match="too large"):
        parse_unified_diff("--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-x\n+" + "y" * 250_000 + "\n")


def test_symlink_in_target_path_blocked(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "t.py").write_text("a\nb\nc\nd\ne\n")
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(outside)
    diff = "--- a/link/t.py\n+++ b/link/t.py\n@@ -2,3 +2,3 @@\n b\n-c\n+C\n d\n"
    with pytest.raises(SecurityViolation):
        apply_to_directory(diff, root)
    assert (outside / "t.py").read_text() == "a\nb\nc\nd\ne\n"


def test_validate_rel_path_accepts_normal_paths():
    assert validate_rel_path("src/pkg/mod.py") == "src/pkg/mod.py"
    assert changed_files(GOOD) == ["f.py"]
