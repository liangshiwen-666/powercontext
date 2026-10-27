"""Unified-diff extraction and safe application helpers.

Model-generated patches use ``a/app/...`` / ``b/app/...`` paths relative to the
demo project root (``minimax/demo``).  Applying is a two step process: a
read-only check first (`patch --dry-run`), then the real application.

**Why GNU ``patch`` by default (and not ``git apply``)**
    The business project lives in a *subdirectory* of the toolkit git
    repository (``repo/minimax/demo``).  ``git apply`` resolves patch paths
    against the **repository root**, so ``a/app/service.py`` becomes
    ``<repo-root>/app/service.py``; the file does not exist, yet ``git apply``
    prints ``Skipped patch 'app/service.py'`` and **exits 0** — a silent no-op
    that would look like success in the demo.  GNU ``patch -p1 -d <work_dir>``
    has no such path-base problem and is used as the primary implementation.
    ``git apply`` is kept as a *fallback only* (when the ``patch`` binary is
    unavailable), with an explicit ``Skipped patch`` failure detector.

Reverse operations use ``patch -R`` so the before/after demo state can be
captured for evidence (``check_only=True`` only probes, never writes).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

__all__ = ["extract_unified_diff", "apply_patch", "reverse_patch"]

_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,})(?P<info>.*)$")
_FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,})\s*$")

# Lines that legally belong to a unified diff (used to trim trailing prose).
_DIFF_PREFIXES = (
    " ",
    "+",
    "-",
    "@",
    "\\",
    "diff --git ",
    "index ",
    "new file mode ",
    "deleted file mode ",
    "old mode ",
    "new mode ",
    "similarity index ",
    "dissimilarity index ",
    "rename from ",
    "rename to ",
    "Binary files ",
    "GIT binary patch",
)

_PATCH_TIMEOUT_S = 120
_GIT_TIMEOUT_S = 120

#: Never trust a zero exit code alone: these markers mean (part of) the patch
#: was skipped/rejected even when ``patch``/``git apply`` returns 0.
_FAILURE_MARKERS = (
    "FAILED",
    "saving rejects",
    "Skipping patch",
    "hunk ignored",
    "can't find file",
    "No file to patch",
    "skipped patch",
)


# ---------------------------------------------------------------------------
# extraction
# ---------------------------------------------------------------------------
def _normalise(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")


def _iter_fenced_blocks(text: str) -> list[tuple[str, str]]:
    """Return ``(info, body)`` for every fenced code block (CommonMark-ish).

    Fences are paired line-by-line; an earlier unlabelled block such as
    ````` ```python ````` must not swallow a later ```` ```diff ```` block.
    """

    lines = text.split("\n")
    blocks: list[tuple[str, str]] = []
    index = 0
    while index < len(lines):
        opened = _FENCE_OPEN_RE.match(lines[index])
        if not opened:
            index += 1
            continue
        fence = opened.group(1)
        info = opened.group("info").strip()
        if "`" in info:  # an inline occurrence, not a fence opener
            index += 1
            continue
        body: list[str] = []
        index += 1
        while index < len(lines):
            closed = _FENCE_CLOSE_RE.match(lines[index])
            if closed and len(closed.group(1)) >= len(fence):
                index += 1
                break
            body.append(lines[index])
            index += 1
        blocks.append((info, "\n".join(body)))
    return blocks


def _looks_like_diff_start(line: str) -> bool:
    return line.startswith("diff --git ") or line.startswith("--- ")


def _is_diff_line(line: str) -> bool:
    return line.startswith(_DIFF_PREFIXES)


def _trim_block(block: str) -> str | None:
    """Take one candidate block, keep only the unified diff part.

    Returns ``None`` when the block does not contain a plausible diff (must
    start with ``diff --git`` or ``--- `` and contain at least one ``@@``
    hunk and a ``+++`` header when it starts with ``--- ``).
    """

    lines = block.split("\n")
    start = None
    for index, line in enumerate(lines):
        if _looks_like_diff_start(line):
            start = index
            break
    if start is None:
        return None
    lines = lines[start:]

    if lines[0].startswith("--- "):
        has_plus = any(line.startswith("+++ ") for line in lines[1:6])
        if not has_plus:
            return None

    # Trim trailing prose (e.g. "希望这个补丁能帮到你") after the last diff line.
    last = len(lines) - 1
    while last >= 0 and not _is_diff_line(lines[last]):
        last -= 1
    if last < 0:
        return None
    body = lines[: last + 1]
    if not any(line.startswith("@@") for line in body):
        return None
    cleaned = "\n".join(body).rstrip("\n")
    if not cleaned:
        return None
    return cleaned + "\n"


def extract_unified_diff(text: str) -> str | None:
    """Extract the first valid unified diff from model output.

    Tolerates surrounding prose and ```diff / ```patch (or unlabelled) code
    fences.  Returns the patch text (with a trailing newline) or ``None`` when
    no unified diff is present.
    """

    if not text or not str(text).strip():
        return None
    normalised = _normalise(str(text))

    for info, body in _iter_fenced_blocks(normalised):
        if info.lower() not in {"", "diff", "patch", "patchdiff"}:
            continue
        candidate = _trim_block(body)
        if candidate is not None:
            return candidate

    # Bare diff without fences, possibly surrounded by prose.
    return _trim_block(normalised)


# ---------------------------------------------------------------------------
# shared result handling
# ---------------------------------------------------------------------------
def _decode(stdout: bytes, stderr: bytes) -> tuple[str, str]:
    return (
        stdout.decode("utf-8", "replace")[-8000:],
        stderr.decode("utf-8", "replace")[-8000:],
    )


def _finalize(
    stdout: str,
    stderr: str,
    returncode: int | None,
    *,
    check_only: bool,
    tool: str,
) -> dict[str, Any]:
    combined = stdout + "\n" + stderr
    markers = [marker for marker in _FAILURE_MARKERS if marker.lower() in combined.lower()]
    ok = returncode == 0 and not markers
    if not ok:
        # GNU patch writes diagnostics ("Hunk #1 FAILED ...") to stdout; make
        # sure callers that only read ``stderr`` still see the reason.
        if stdout and stdout not in stderr:
            stderr = (stderr + "\n" + stdout).strip()
        if markers:
            stderr = (stderr + "\nfailure markers: " + ", ".join(markers)).strip()
    return {
        "ok": ok,
        "stderr": stderr,
        "stdout": stdout,
        "applied": (not check_only) and ok,
        "returncode": returncode,
        "tool": tool,
    }


def _missing_result(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "stderr": message,
        "stdout": "",
        "applied": False,
        "returncode": None,
        "tool": None,
    }


# ---------------------------------------------------------------------------
# GNU patch (primary implementation)
# ---------------------------------------------------------------------------
def _run_gnu_patch(
    work_dir: Path,
    patch: str,
    *,
    reverse: bool,
    check_only: bool,
) -> dict[str, Any]:
    command = ["patch", "-p1"]
    if reverse:
        command.append("-R")
    command += [
        "-d",
        str(work_dir),
        "--batch",
        "--forward",  # never silently assume -R on an already-applied patch
        "--no-backup-if-mismatch",
        "-r",
        "-",  # reject file: stdout (keeps the worktree clean)
    ]
    if check_only:
        command.append("--dry-run")

    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".diff", delete=False, prefix="sddctx-"
    )
    try:
        handle.write(patch)
        handle.close()
        command += ["-i", handle.name]
        try:
            proc = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=_PATCH_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return _missing_result(f"failed to execute GNU patch: {type(exc).__name__}: {exc}")
        stdout, stderr = _decode(proc.stdout, proc.stderr)
        return _finalize(stdout, stderr, proc.returncode, check_only=check_only, tool="patch")
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# git apply (fallback only)
# ---------------------------------------------------------------------------
def _git_root_and_prefix(work_dir: Path) -> tuple[Path | None, str]:
    """Return ``(repo_root, prefix)`` for ``work_dir`` (``(None, "")`` if not a repo)."""

    try:
        proc = subprocess.run(
            ["git", "-C", str(work_dir), "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None, ""
    if proc.returncode != 0:
        return None, ""
    root_text = proc.stdout.decode("utf-8", "replace").strip()
    if not root_text:
        return None, ""
    root = Path(root_text)
    try:
        prefix = work_dir.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None, ""
    return root, "" if prefix == "." else prefix


def _run_git_apply(
    work_dir: Path,
    patch_text: str,
    args: list[str],
    root: Path | None,
    prefix: str,
) -> subprocess.CompletedProcess[bytes]:
    if root is not None:
        command = ["git", "-C", str(root), "apply", *args, "--verbose"]
        if prefix:
            command.append(f"--directory={prefix}")
    else:
        command = ["git", "apply", *args, "--verbose"]
    command.append("-")
    return subprocess.run(
        command,
        cwd=str(work_dir),
        input=patch_text.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=_GIT_TIMEOUT_S,
    )


def _git_apply(
    work_dir: Path,
    patch: str,
    *,
    reverse: bool,
    check_only: bool,
) -> dict[str, Any]:
    if shutil.which("git") is None:
        return _missing_result("neither GNU patch nor git is available")
    root, prefix = _git_root_and_prefix(work_dir)
    base = ["-R"] if reverse else []
    check = _run_git_apply(work_dir, patch, [*base, "--check"], root, prefix)
    stdout, stderr = _decode(check.stdout, check.stderr)
    check_result = _finalize(stdout, stderr, check.returncode, check_only=check_only, tool="git")
    if not check_result["ok"]:
        return check_result
    if check_only:
        return check_result

    proc = _run_git_apply(work_dir, patch, [*base, "--whitespace=nowarn"], root, prefix)
    stdout, stderr = _decode(proc.stdout, proc.stderr)
    result = _finalize(stdout, stderr, proc.returncode, check_only=False, tool="git")
    if not result["ok"] and not result["stderr"]:
        result["stderr"] = check_result["stderr"]
    return result


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
def _apply(
    work_dir: Path | str,
    patch_text: str,
    *,
    reverse: bool,
    check_only: bool,
) -> dict[str, Any]:
    work_path = Path(work_dir)
    patch = extract_unified_diff(patch_text)
    if patch is None:
        return _missing_result("no unified diff found in patch text")

    if not work_path.is_dir():
        return _missing_result(f"work_dir does not exist or is not a directory: {work_path}")

    if shutil.which("patch") is not None:
        return _run_gnu_patch(work_path, patch, reverse=reverse, check_only=check_only)
    # Fallback: git apply (kept for environments without GNU patch).
    return _git_apply(work_path, patch, reverse=reverse, check_only=check_only)


def apply_patch(work_dir: Path | str, patch_text: str, *, check_only: bool = False) -> dict[str, Any]:
    """Apply ``patch_text`` inside ``work_dir``.

    Primary implementation is GNU ``patch -p1 -d <work_dir>`` (see the module
    docstring for why ``git apply`` is only a fallback).  A read-only
    ``--dry-run`` check always runs first; the real application only starts
    when the check passes and ``check_only=False``.

    Returns
    -------
    dict
        ``{"ok", "stderr", "stdout", "applied", "returncode", "tool"}``.
        ``ok`` is true only when the command succeeded **and** no
        ``FAILED``/``saving rejects``/``Skipping patch`` marker was emitted.
    """

    return _apply(work_dir, patch_text, reverse=False, check_only=check_only)


def reverse_patch(
    work_dir: Path | str,
    patch_text: str,
    *,
    check_only: bool = False,
) -> dict[str, Any]:
    """Reverse-apply ``patch_text`` inside ``work_dir`` (``patch -R``).

    Used by the demo flow to restore the pre-patch state while keeping the
    after-state in evidence.  With ``check_only=True`` the tree is only tested
    (``patch -R --dry-run``) and never modified — the CLI uses this to tell
    whether the demo is currently in the before or after state.
    """

    return _apply(work_dir, patch_text, reverse=True, check_only=check_only)
