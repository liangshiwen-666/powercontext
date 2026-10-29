"""Offline tests for ``sddctx.patch`` — the pure-Python unified-diff engine.

These tests must pass on a machine without GNU ``patch`` or ``git``: apply and
reverse never shell out.  The final test replays the real competition patch on a
copy of ``demo/`` and runs the business test suites, proving the engine produces
the same before/after behaviour as the recorded evidence.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sddctx.patch import apply_patch, extract_unified_diff, reverse_patch

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO = REPO_ROOT / "demo"
REAL_PATCH = REPO_ROOT / "patches" / "0001-orders-filters.patch"

ORIGINAL = "def add(a, b):\n    return a - b\n"
PATCHED = "def add(a, b):\n    return a + b\n"

DIFF = (
    "diff --git a/app/x.py b/app/x.py\n"
    "--- a/app/x.py\n"
    "+++ b/app/x.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def add(a, b):\n"
    "-    return a - b\n"
    "+    return a + b\n"
)

FENCED = "这是模型给出的补丁：\n\n```diff\n" + DIFF + "```\n\n希望有帮助。"


def read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def snapshot(root: Path, *, subdirs: tuple[str, ...] = (".",)) -> dict[str, str]:
    result: dict[str, str] = {}
    for sub in subdirs:
        base = root if sub == "." else root / sub
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or ".pytest_cache" in path.parts or ".venv" in path.parts:
                continue
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


@pytest.fixture()
def demo(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    (root / "app").mkdir(parents=True)
    (root / "app" / "x.py").write_text(ORIGINAL, encoding="utf-8")
    return root


@pytest.fixture()
def nested_demo(tmp_path: Path) -> Path:
    """Real toolkit layout: ``demo`` is a subdirectory of a git repository."""

    repo = tmp_path / "toolkit"
    (repo / "demo" / "app").mkdir(parents=True)
    (repo / "demo" / "app" / "x.py").write_text(ORIGINAL, encoding="utf-8")
    if shutil.which("git") is not None:
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True, capture_output=True)
    return repo / "demo"


# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------
def test_extract_from_diff_fence():
    extracted = extract_unified_diff(FENCED)
    assert extracted is not None
    assert extracted.startswith("diff --git a/app/x.py")
    assert "+    return a + b" in extracted
    assert extracted.endswith("\n")
    assert "希望有帮助" not in extracted


def test_extract_from_patch_fence():
    text = "前言\n```patch\n" + DIFF + "```\n"
    assert extract_unified_diff(text) == DIFF


def test_extract_from_unlabelled_fence():
    assert extract_unified_diff("```\n" + DIFF + "```") == DIFF


def test_extract_bare_diff_with_surrounding_prose():
    text = "我先说明一下思路。\n\n" + DIFF + "\n以上是补丁，请应用。\n"
    extracted = extract_unified_diff(text)
    assert extracted is not None
    assert extracted.startswith("diff --git a/app/x.py")
    assert "以上是补丁" not in extracted


def test_extract_multiple_blocks_picks_the_diff():
    text = (
        "```python\nprint('not a diff')\n```\n\n"
        "```diff\n" + DIFF + "```\n"
        "```text\nanother non diff\n```\n"
    )
    assert extract_unified_diff(text) == DIFF


def test_extract_multiple_diff_fences_returns_first():
    other = DIFF.replace("x.py", "y.py").replace("return a - b", "return a - c").replace(
        "return a + b", "return a + c"
    )
    text = "```diff\n" + DIFF + "```\n中间说明\n```diff\n" + other + "```"
    assert extract_unified_diff(text) == DIFF


def test_extract_returns_none_without_diff():
    assert extract_unified_diff("") is None
    assert extract_unified_diff("模型说：我无法完成这个任务。") is None
    assert extract_unified_diff("```python\ndef add(a, b):\n    return a + b\n```") is None


def test_extract_returns_none_for_truncated_header():
    assert extract_unified_diff("--- a/app/x.py\n+++ b/app/x.py\n（没有 hunk）\n") is None


# --------------------------------------------------------------------------
# pure-Python engine: basics
# --------------------------------------------------------------------------
def test_tool_is_python_and_check_only_does_not_modify(demo: Path):
    result = apply_patch(demo, FENCED, check_only=True)
    assert result["ok"] is True, result
    assert result["tool"] == "python"
    assert result["applied"] is False
    assert result["returncode"] == 0
    assert read_bytes(demo / "app" / "x.py") == ORIGINAL.encode()


def test_apply_then_reverse_is_byte_identical(demo: Path):
    before = read_bytes(demo / "app" / "x.py")
    applied = apply_patch(demo, FENCED)
    assert applied["ok"] is True, applied
    assert applied["applied"] is True
    assert read_bytes(demo / "app" / "x.py") == PATCHED.encode()

    reversed_ = reverse_patch(demo, FENCED)
    assert reversed_["ok"] is True, reversed_
    assert reversed_["applied"] is True
    assert read_bytes(demo / "app" / "x.py") == before


def test_second_apply_fails_cleanly_not_silently_reversed(demo: Path):
    assert apply_patch(demo, FENCED)["ok"] is True
    second = apply_patch(demo, FENCED)
    assert second["ok"] is False
    assert second["applied"] is False
    assert "context mismatch" in second["stderr"]
    assert read_bytes(demo / "app" / "x.py") == PATCHED.encode()


def test_context_mismatch_reports_file_hunk_and_line(demo: Path):
    bad_patch = DIFF.replace("-    return a - b", "-    return a - zzz")
    result = apply_patch(demo, bad_patch)
    assert result["ok"] is False
    assert result["applied"] is False
    message = result["stderr"]
    assert "context mismatch" in message
    assert "app/x.py" in message
    assert "hunk #1" in message
    assert ":2" in message or "line 2" in message
    assert "return a - zzz" in message
    assert read_bytes(demo / "app" / "x.py") == ORIGINAL.encode()


def test_reverse_on_clean_tree_fails_without_touching_files(demo: Path):
    result = reverse_patch(demo, FENCED, check_only=True)
    assert result["ok"] is False
    assert result["applied"] is False
    assert read_bytes(demo / "app" / "x.py") == ORIGINAL.encode()
    assert "context mismatch" in result["stderr"]


def test_reverse_check_only_detects_after_state(demo: Path):
    assert apply_patch(demo, FENCED)["ok"] is True
    detected = reverse_patch(demo, FENCED, check_only=True)
    assert detected["ok"] is True
    assert detected["applied"] is False
    assert read_bytes(demo / "app" / "x.py") == PATCHED.encode()


def test_apply_without_any_diff_returns_error(demo: Path):
    result = apply_patch(demo, "没有补丁，只有解释。")
    assert result["ok"] is False
    assert result["applied"] is False
    assert "no unified diff" in result["stderr"]


def test_engine_works_inside_git_repo_subdirectory(nested_demo: Path):
    applied = apply_patch(nested_demo, FENCED)
    assert applied["ok"] is True, applied
    assert applied["tool"] == "python"
    assert read_bytes(nested_demo / "app" / "x.py") == PATCHED.encode()
    assert reverse_patch(nested_demo, FENCED)["ok"] is True
    assert read_bytes(nested_demo / "app" / "x.py") == ORIGINAL.encode()


def test_git_style_headers_with_index_and_timestamps(demo: Path):
    """Model output may be copied from `git diff`: index lines + tab timestamps."""

    patch = (
        "diff --git a/app/x.py b/app/x.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/app/x.py\t2026-10-27 10:00:00.000000000 +0800\n"
        "+++ b/app/x.py\t2026-10-27 10:01:00.000000000 +0800\n"
        "@@ -1,2 +1,2 @@\n"
        " def add(a, b):\n"
        "-    return a - b\n"
        "+    return a + b\n"
    )
    assert apply_patch(demo, patch)["ok"] is True
    assert read_bytes(demo / "app" / "x.py") == PATCHED.encode()
    assert reverse_patch(demo, patch)["ok"] is True
    assert read_bytes(demo / "app" / "x.py") == ORIGINAL.encode()


def test_utf8_content_round_trip(tmp_path: Path):
    demo = tmp_path / "demo"
    (demo / "app").mkdir(parents=True)
    original = "描述 = '旧值'\n".encode("utf-8")
    (demo / "app" / "x.py").write_bytes(original)
    patch = (
        "--- a/app/x.py\n"
        "+++ b/app/x.py\n"
        "@@ -1 +1 @@\n"
        "-描述 = '旧值'\n"
        "+描述 = '新值'\n"
    )
    assert apply_patch(demo, patch)["ok"] is True
    assert (demo / "app" / "x.py").read_bytes() == "描述 = '新值'\n".encode("utf-8")
    assert reverse_patch(demo, patch)["ok"] is True
    assert read_bytes(demo / "app" / "x.py") == original


# --------------------------------------------------------------------------
# CRLF / EOF-newline handling
# --------------------------------------------------------------------------
def test_crlf_file_apply_and_reverse_preserves_bytes(tmp_path: Path):
    demo = tmp_path / "demo"
    (demo / "app").mkdir(parents=True)
    crlf_original = b"def add(a, b):\r\n    return a - b\r\n"
    (demo / "app" / "x.py").write_bytes(crlf_original)

    applied = apply_patch(demo, DIFF)
    assert applied["ok"] is True, applied
    assert read_bytes(demo / "app" / "x.py") == b"def add(a, b):\r\n    return a + b\r\n"

    reversed_ = reverse_patch(demo, DIFF)
    assert reversed_["ok"] is True, reversed_
    assert read_bytes(demo / "app" / "x.py") == crlf_original


def test_no_newline_at_eof_round_trip(tmp_path: Path):
    demo = tmp_path / "demo"
    (demo / "app").mkdir(parents=True)
    original = b"def add(a, b):\n    return a - b"  # no trailing newline
    (demo / "app" / "x.py").write_bytes(original)
    patch = (
        "--- a/app/x.py\n"
        "+++ b/app/x.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def add(a, b):\n"
        "-    return a - b\n"
        "\\ No newline at end of file\n"
        "+    return a + b\n"
        "\\ No newline at end of file\n"
    )
    assert apply_patch(demo, patch)["ok"] is True
    assert read_bytes(demo / "app" / "x.py") == b"def add(a, b):\n    return a + b"
    assert reverse_patch(demo, patch)["ok"] is True
    assert read_bytes(demo / "app" / "x.py") == original


# --------------------------------------------------------------------------
# create / delete / multi-file
# --------------------------------------------------------------------------
NEW_FILE_PATCH = (
    "diff --git a/app/new_module.py b/app/new_module.py\n"
    "new file mode 100644\n"
    "index 0000000..1111111\n"
    "--- /dev/null\n"
    "+++ b/app/new_module.py\n"
    "@@ -0,0 +1,3 @@\n"
    '+"""new module."""\n'
    "+\n"
    "+VALUE = 42\n"
)

DELETE_FILE_PATCH = (
    "diff --git a/app/old.py b/app/old.py\n"
    "deleted file mode 100644\n"
    "index 1111111..0000000\n"
    "--- a/app/old.py\n"
    "+++ /dev/null\n"
    "@@ -1,2 +0,0 @@\n"
    "-def old():\n"
    "-    return 1\n"
)


def test_create_new_file_and_reverse_deletes_it(demo: Path):
    assert not (demo / "app" / "new_module.py").exists()
    check = apply_patch(demo, NEW_FILE_PATCH, check_only=True)
    assert check["ok"] is True, check
    assert not (demo / "app" / "new_module.py").exists()

    applied = apply_patch(demo, NEW_FILE_PATCH)
    assert applied["ok"] is True, applied
    new_file = demo / "app" / "new_module.py"
    assert new_file.read_bytes() == b'"""new module."""\n\nVALUE = 42\n'

    reversed_ = reverse_patch(demo, NEW_FILE_PATCH)
    assert reversed_["ok"] is True, reversed_
    assert not new_file.exists()


def test_delete_file_and_reverse_recreates_it(demo: Path):
    old_file = demo / "app" / "old.py"
    old_file.write_text("def old():\n    return 1\n", encoding="utf-8")
    applied = apply_patch(demo, DELETE_FILE_PATCH)
    assert applied["ok"] is True, applied
    assert not old_file.exists()

    reversed_ = reverse_patch(demo, DELETE_FILE_PATCH)
    assert reversed_["ok"] is True, reversed_
    assert old_file.read_bytes() == b"def old():\n    return 1\n"


def test_multi_file_patch(tmp_path: Path):
    demo = tmp_path / "demo"
    (demo / "app").mkdir(parents=True)
    (demo / "app" / "x.py").write_text(ORIGINAL, encoding="utf-8")
    (demo / "app" / "y.py").write_text("VALUE = 1\n", encoding="utf-8")
    multi = DIFF + (
        "diff --git a/app/y.py b/app/y.py\n"
        "--- a/app/y.py\n"
        "+++ b/app/y.py\n"
        "@@ -1 +1 @@\n"
        "-VALUE = 1\n"
        "+VALUE = 2\n"
    )
    assert apply_patch(demo, multi)["ok"] is True
    assert read_bytes(demo / "app" / "x.py") == PATCHED.encode()
    assert read_bytes(demo / "app" / "y.py") == b"VALUE = 2\n"
    assert reverse_patch(demo, multi)["ok"] is True
    assert read_bytes(demo / "app" / "x.py") == ORIGINAL.encode()
    assert read_bytes(demo / "app" / "y.py") == b"VALUE = 1\n"


# --------------------------------------------------------------------------
# clear failures: missing file / unsafe paths / unsupported patch types
# --------------------------------------------------------------------------
def test_missing_source_file_is_reported(demo: Path):
    missing = DIFF.replace("app/x.py", "app/missing.py")
    result = apply_patch(demo, missing)
    assert result["ok"] is False
    assert "does not exist" in result["stderr"]
    assert "app/missing.py" in result["stderr"]


def test_path_traversal_is_rejected(demo: Path):
    hostile = DIFF.replace("--- a/app/x.py", "--- a/../evil.py").replace(
        "+++ b/app/x.py", "+++ b/../evil.py"
    )
    result = apply_patch(demo, hostile)
    assert result["ok"] is False
    assert "path traversal" in result["stderr"]
    assert not (demo.parent / "evil.py").exists()


def test_absolute_path_is_rejected(demo: Path):
    hostile = DIFF.replace("+++ b/app/x.py", "+++ /tmp/evil.py")
    result = apply_patch(demo, hostile)
    assert result["ok"] is False
    assert "absolute" in result["stderr"].lower()


def test_rename_patch_is_rejected(demo: Path):
    rename = (
        "diff --git a/app/x.py b/app/y.py\n"
        "similarity index 100%\n"
        "rename from app/x.py\n"
        "rename to app/y.py\n"
        "--- a/app/x.py\n"
        "+++ b/app/y.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def add(a, b):\n"
        "-    return a - b\n"
        "+    return a + b\n"
    )
    result = apply_patch(demo, rename)
    assert result["ok"] is False
    assert "rename" in result["stderr"].lower()


def test_binary_patch_is_rejected(demo: Path):
    binary = (
        "diff --git a/app/x.py b/app/x.py\n"
        "new file mode 100644\n"
        "GIT binary patch\n"
        "literal 10\n"
        "--- a/app/x.py\n"
        "+++ b/app/x.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def add(a, b):\n"
        "-    return a - b\n"
        "+    return a + b\n"
    )
    result = apply_patch(demo, binary)
    assert result["ok"] is False
    assert "binary" in result["stderr"].lower()


# --------------------------------------------------------------------------
# end-to-end: replay the real competition patch on a copy of demo/
# --------------------------------------------------------------------------
def _run_pytest(demo: Path, suite: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", suite, "-q", "--tb=line", "-p", "no:cacheprovider"],
        cwd=demo,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


@pytest.mark.skipif(
    not DEMO.is_dir() or not REAL_PATCH.is_file(),
    reason="demo/ or patches/0001-orders-filters.patch is not available",
)
def test_real_project_patch_before_after(tmp_path: Path):
    demo = tmp_path / "demo"
    shutil.copytree(DEMO, demo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    patch_text = REAL_PATCH.read_text(encoding="utf-8")

    # Normalise the observed tree state to "before" (the committed demo tree is
    # the post-change state).
    if reverse_patch(demo, patch_text, check_only=True)["ok"]:
        normalised = reverse_patch(demo, patch_text)
        assert normalised["ok"] is True, normalised["stderr"]
    else:
        assert apply_patch(demo, patch_text, check_only=True)["ok"], "demo tree is neither before nor after"

    before_snapshot = snapshot(demo, subdirs=("app", "tests"))
    assert _run_pytest(demo, "tests/target").returncode != 0

    applied = apply_patch(demo, patch_text)
    assert applied["ok"] is True, applied["stderr"]
    after_snapshot = snapshot(demo, subdirs=("app", "tests"))
    assert after_snapshot != before_snapshot
    assert _run_pytest(demo, "tests/target").returncode == 0
    assert _run_pytest(demo, "tests/regression").returncode == 0

    reversed_ = reverse_patch(demo, patch_text)
    assert reversed_["ok"] is True, reversed_["stderr"]
    assert snapshot(demo, subdirs=("app", "tests")) == before_snapshot
    assert _run_pytest(demo, "tests/target").returncode != 0
