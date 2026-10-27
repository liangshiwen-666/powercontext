"""Offline tests for ``sddctx.patch`` (real filesystem, no network).

The business project lives in a *subdirectory* of a git repository
(``repo/minimax/demo``).  ``git apply`` resolves patch paths relative to the
repository root and silently skips them there, so the primary implementation
uses GNU ``patch -p1 -d <dir>``; these tests cover both that default and the
``git apply`` fallback.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import sddctx.patch as patch_module
from sddctx.patch import apply_patch, extract_unified_diff, reverse_patch

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

HAS_PATCH = shutil.which("patch") is not None
requires_patch = pytest.mark.skipif(not HAS_PATCH, reason="GNU patch is not available")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


@pytest.fixture()
def demo(tmp_path: Path) -> Path:
    """Plain directory (no git repository): the ``patch -d <dir>`` happy path."""

    repo = tmp_path / "demo"
    (repo / "app").mkdir(parents=True)
    (repo / "app" / "x.py").write_text(ORIGINAL, encoding="utf-8")
    return repo


@pytest.fixture()
def nested_demo(tmp_path: Path) -> Path:
    """Real toolkit layout: ``demo`` is a subdirectory of a git repository.

    This is exactly the layout where ``git apply`` used to print
    ``Skipped patch 'app/x.py'`` and still exit 0.
    """

    if shutil.which("git") is None:
        pytest.skip("git is not available")
    repo = tmp_path / "toolkit"
    (repo / "demo" / "app").mkdir(parents=True)
    (repo / "demo" / "app" / "x.py").write_text(ORIGINAL, encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "tests@example.com")
    _git(repo, "config", "user.name", "sddctx tests")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo / "demo"


def read_x(repo: Path) -> str:
    return (repo / "app" / "x.py").read_text(encoding="utf-8")


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
    text = "```\n" + DIFF + "```"
    assert extract_unified_diff(text) == DIFF


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


def test_extract_returns_none_without_diff():
    assert extract_unified_diff("") is None
    assert extract_unified_diff("模型说：我无法完成这个任务。") is None
    assert extract_unified_diff("```python\ndef add(a, b):\n    return a + b\n```") is None


def test_extract_returns_none_for_truncated_header():
    truncated = "--- a/app/x.py\n+++ b/app/x.py\n（没有 hunk）\n"
    assert extract_unified_diff(truncated) is None


# --------------------------------------------------------------------------
# GNU patch default path
# --------------------------------------------------------------------------
@requires_patch
def test_default_tool_is_gnu_patch(demo: Path):
    result = apply_patch(demo, FENCED, check_only=True)
    assert result["tool"] == "patch"
    assert result["ok"] is True
    assert result["applied"] is False
    assert read_x(demo) == ORIGINAL


@requires_patch
def test_apply_in_plain_directory_without_git(demo: Path):
    result = apply_patch(demo, FENCED)
    assert result["ok"] is True, result
    assert result["applied"] is True
    assert result["tool"] == "patch"
    assert read_x(demo) == PATCHED
    assert not list(demo.rglob("*.rej")), "rejects must not be written into the worktree"
    assert not list(demo.rglob("*.orig")), "backup files must not be written into the worktree"


@requires_patch
def test_apply_then_reverse_round_trip(demo: Path):
    apply_result = apply_patch(demo, FENCED)
    assert apply_result["ok"] is True, apply_result
    assert apply_result["applied"] is True
    assert read_x(demo) == PATCHED

    reverse_result = reverse_patch(demo, FENCED)
    assert reverse_result["ok"] is True, reverse_result
    assert reverse_result["applied"] is True
    assert read_x(demo) == ORIGINAL


@requires_patch
def test_second_apply_is_rejected_not_silently_reversed(demo: Path):
    """``patch --batch`` would otherwise assume ``-R``; we pass ``--forward``."""

    first = apply_patch(demo, FENCED)
    assert first["ok"] is True

    second = apply_patch(demo, FENCED)
    assert second["ok"] is False
    assert second["applied"] is False
    # The file must still be in the *patched* state (no accidental reverse).
    assert read_x(demo) == PATCHED
    assert second["returncode"] != 0


@requires_patch
def test_reverse_on_clean_tree_fails_without_touching_files(demo: Path):
    result = reverse_patch(demo, FENCED)
    assert result["ok"] is False
    assert result["applied"] is False
    assert read_x(demo) == ORIGINAL
    assert result["stderr"] or result["stdout"]


@requires_patch
def test_apply_invalid_context_fails_cleanly(demo: Path):
    bad_patch = DIFF.replace("-    return a - b", "-    return a - z")
    result = apply_patch(demo, bad_patch)
    assert result["ok"] is False
    assert result["applied"] is False
    assert "FAILED" in (result["stdout"] + result["stderr"])
    assert read_x(demo) == ORIGINAL


@requires_patch
def test_reverse_check_only_detects_state_without_modifying(demo: Path):
    """``cli.py`` uses ``reverse_patch(..., check_only=True)`` for before/after."""

    applied = apply_patch(demo, FENCED)
    assert applied["ok"] is True

    detected = reverse_patch(demo, FENCED, check_only=True)
    assert detected["ok"] is True
    assert detected["applied"] is False
    assert read_x(demo) == PATCHED  # check does not change the worktree

    restored = reverse_patch(demo, FENCED)
    assert restored["ok"] is True and restored["applied"] is True
    assert read_x(demo) == ORIGINAL


@requires_patch
def test_apply_without_any_diff_returns_error(demo: Path):
    result = apply_patch(demo, "没有补丁，只有解释。")
    assert result["ok"] is False
    assert result["applied"] is False
    assert "no unified diff" in result["stderr"]


# --------------------------------------------------------------------------
# real-layout regression (demo is a subdirectory of a git repo)
# --------------------------------------------------------------------------
@requires_patch
def test_apply_inside_git_repo_subdirectory(nested_demo: Path):
    """Even inside ``repo/demo`` GNU patch only touches ``demo/app/x.py``.

    The previous ``git apply`` implementation resolved ``a/app/x.py`` against
    the repository root, printed ``Skipped patch`` and exited 0.
    """

    check = apply_patch(nested_demo, FENCED, check_only=True)
    assert check["ok"] is True, check
    assert read_x(nested_demo) == ORIGINAL

    applied = apply_patch(nested_demo, FENCED)
    assert applied["ok"] is True, applied
    assert applied["applied"] is True
    assert "Skipped patch" not in (applied["stdout"] + applied["stderr"])
    assert read_x(nested_demo) == PATCHED

    reversed_result = reverse_patch(nested_demo, FENCED)
    assert reversed_result["ok"] is True, reversed_result
    assert read_x(nested_demo) == ORIGINAL


# --------------------------------------------------------------------------
# git apply fallback (only used when GNU patch is unavailable)
# --------------------------------------------------------------------------
def test_git_apply_fallback_when_patch_is_missing(nested_demo: Path, monkeypatch):
    real_which = shutil.which

    def fake_which(name: str):
        if name == "patch":
            return None
        return real_which(name)

    monkeypatch.setattr(patch_module.shutil, "which", fake_which)

    result = apply_patch(nested_demo, FENCED)
    assert result["tool"] == "git", result
    assert result["ok"] is True, result
    assert result["applied"] is True
    assert read_x(nested_demo) == PATCHED

    reversed_result = reverse_patch(nested_demo, FENCED)
    assert reversed_result["tool"] == "git"
    assert reversed_result["ok"] is True
    assert read_x(nested_demo) == ORIGINAL
