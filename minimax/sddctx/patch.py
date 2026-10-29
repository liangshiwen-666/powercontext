"""Unified-diff extraction and **pure-Python** application helpers.

Why no external tools?
    V1 review machines may not ship GNU ``patch`` (macOS has BSD ``patch`` with
    different flags) and ``git apply`` resolves paths against the enclosing git
    worktree — inside this project (``repo/minimax/demo``) it silently skips
    ``a/app/...`` paths and still exits 0.  :func:`apply_patch` /
    :func:`reverse_patch` therefore implement unified-diff application in
    Python only: no ``patch`` binary, no ``git``, no repository required.
    The demo patch format is fully supported: ``diff --git`` headers,
    ``a/``/``b/`` prefixes, multiple files, exact-context hunks, file
    creation/deletion and CRLF line endings.

Semantics
    * Matching is **exact** (byte content per line, line endings ignored for
      matching but preserved when writing); a context mismatch is reported with
      file, hunk and line number — no fuzzing, no silent skips.
    * Apply and reverse are byte-identical round trips for LF and CRLF files
      (created files are written with ``\\n``).
    * ``check_only=True`` simulates everything in memory and never touches the
      worktree.
    * Multi-file patches are validated first and written only after every file
      section passed, so a failure cannot leave a partially patched tree
      (barring I/O errors).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

__all__ = ["apply_patch", "extract_unified_diff", "reverse_patch"]

# ---------------------------------------------------------------------------
# unified-diff extraction (tolerant of prose / fences / multiple blocks)
# ---------------------------------------------------------------------------
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
# patch model
# ---------------------------------------------------------------------------
class _PatchError(ValueError):
    """Actionable parse/apply error (message is user-facing, no key material)."""


@dataclass
class _HunkLine:
    op: str  # " " (context), "-" (removed), "+" (added)
    content: str
    no_newline: bool = False


@dataclass
class _Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    lines: list[_HunkLine] = field(default_factory=list)


@dataclass
class _FilePatch:
    old_path: str | None  # None => /dev/null
    new_path: str | None  # None => /dev/null
    hunks: list[_Hunk]


@dataclass
class _FilePlan:
    path: str
    content: str | None  # None => delete the file
    change: str  # modify | create | delete
    hunks: int


_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)

_META_PREFIXES = (
    "index ",
    "new file mode ",
    "deleted file mode ",
    "old mode ",
    "new mode ",
    "similarity index ",
    "dissimilarity index ",
)


def _strip_path_token(raw: str) -> str | None:
    """``--- a/app/x.py\\t2026-...`` -> ``app/x.py``; ``/dev/null`` -> ``None``."""

    token = raw.split("\t")[0].strip()
    if token.startswith('"') and token.endswith('"') and len(token) >= 2:
        token = token[1:-1]
    if token == "/dev/null":
        return None
    for prefix in ("a/", "b/"):
        if token.startswith(prefix):
            token = token[len(prefix) :]
            break
    if not token:
        raise _PatchError("empty file path in diff header")
    return token


def _validate_hunk_counts(hunk: _Hunk, where: str) -> None:
    if not hunk.lines:
        raise _PatchError(f"{where}: hunk has no body lines")
    context = sum(1 for line in hunk.lines if line.op == " ")
    removed = sum(1 for line in hunk.lines if line.op == "-")
    added = sum(1 for line in hunk.lines if line.op == "+")
    if context + removed != hunk.old_count:
        raise _PatchError(
            f"{where}: hunk header says -{hunk.old_start},{hunk.old_count} "
            f"but body has {context} context + {removed} removed lines"
        )
    if context + added != hunk.new_count:
        raise _PatchError(
            f"{where}: hunk header says +{hunk.new_start},{hunk.new_count} "
            f"but body has {context} context + {added} added lines"
        )


def _parse_patch(patch_text: str) -> list[_FilePatch]:
    """Parse a unified diff into file sections (strict but well explained)."""

    text = _normalise(patch_text).rstrip("\n")
    lines = text.split("\n")
    sections: list[_FilePatch] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        if line.strip() == "":
            index += 1
            continue

        if line.startswith("diff --git "):
            index += 1
            # Optional git metadata before the --- header.
            while index < len(lines) and lines[index].startswith(_META_PREFIXES):
                index += 1
            if index < len(lines) and lines[index].startswith(("rename from ", "rename to ")):
                raise _PatchError("renamed-file patches are not supported (only modify/create/delete)")
            if index < len(lines) and lines[index].startswith(("Binary files ", "GIT binary patch")):
                raise _PatchError("binary patches are not supported")
            if index >= len(lines):
                raise _PatchError("patch ends after a 'diff --git' header without a ---/+++ pair")

        if not lines[index].startswith("--- "):
            raise _PatchError(f"expected a '--- ' file header at patch line {index + 1}: {lines[index][:80]!r}")
        old_path = _strip_path_token(lines[index][4:])
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise _PatchError(f"expected a '+++ ' file header after the '--- ' line: {lines[index - 1][:80]!r}")
        new_path = _strip_path_token(lines[index][4:])
        index += 1
        if old_path is None and new_path is None:
            raise _PatchError("file section uses /dev/null on both sides")
        where = f"file {new_path or old_path}"

        hunks: list[_Hunk] = []
        while index < len(lines) and lines[index].startswith("@@"):
            header = lines[index]
            match = _HUNK_RE.match(header)
            if not match:
                raise _PatchError(f"{where}: malformed hunk header {header[:100]!r} at patch line {index + 1}")
            hunk = _Hunk(
                old_start=int(match.group("old_start")),
                old_count=int(match.group("old_count") or "1"),
                new_start=int(match.group("new_start")),
                new_count=int(match.group("new_count") or "1"),
                header=header,
            )
            index += 1
            while index < len(lines):
                body_line = lines[index]
                if body_line.startswith(("@@", "diff --git ", "--- ")):
                    break
                if body_line.startswith("\\"):
                    if not hunk.lines:
                        raise _PatchError(f"{where}: no-newline marker without a preceding hunk line")
                    hunk.lines[-1].no_newline = True
                    index += 1
                    continue
                if body_line.startswith(" "):
                    hunk.lines.append(_HunkLine(" ", body_line[1:]))
                elif body_line.startswith("+"):
                    hunk.lines.append(_HunkLine("+", body_line[1:]))
                elif body_line.startswith("-"):
                    hunk.lines.append(_HunkLine("-", body_line[1:]))
                else:
                    raise _PatchError(
                        f"{where}: unexpected line inside a hunk at patch line {index + 1}: {body_line[:100]!r}"
                    )
                index += 1
            _validate_hunk_counts(hunk, f"{where} {header}")
            hunks.append(hunk)

        if not hunks:
            raise _PatchError(f"{where}: file section has no hunks")
        sections.append(_FilePatch(old_path=old_path, new_path=new_path, hunks=hunks))

        # A bare '--- ' section (no diff --git line) starts the next file.
        if index < len(lines) and not (
            lines[index].startswith(("diff --git ", "--- "))
        ):
            raise _PatchError(f"unexpected content after hunk body at patch line {index + 1}: {lines[index][:100]!r}")

    if not sections:
        raise _PatchError("no file sections found in patch")
    return sections


# ---------------------------------------------------------------------------
# filesystem / line handling
# ---------------------------------------------------------------------------
def _resolve_target(work_dir: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute():
        raise _PatchError(f"absolute paths are not allowed in patches: {relative!r}")
    if any(part == ".." for part in pure.parts):
        raise _PatchError(f"path traversal ('..') is not allowed in patches: {relative!r}")
    if not pure.parts or pure.parts[0] in {"", "."}:
        raise _PatchError(f"invalid patch path: {relative!r}")
    if ".git" in pure.parts:
        raise _PatchError(f"refusing to patch inside .git: {relative!r}")
    target = work_dir / Path(*pure.parts)
    base = work_dir.resolve()
    try:
        resolved = target.resolve()
    except OSError:  # pragma: no cover - exotic FS errors
        resolved = target.absolute()
    if base != resolved and base not in resolved.parents:
        raise _PatchError(f"patch path escapes work_dir: {relative!r}")
    return target


def _split_lines(text: str) -> list[tuple[str, str]]:
    """Split text into ``(content, eol)``; ``eol`` is ``\\n``, ``\\r\\n`` or ``""``."""

    lines: list[tuple[str, str]] = []
    if text == "":
        return lines
    start = 0
    while True:
        newline = text.find("\n", start)
        if newline == -1:
            lines.append((text[start:], ""))
            return lines
        raw = text[start:newline]
        if raw.endswith("\r"):
            lines.append((raw[:-1], "\r\n"))
        else:
            lines.append((raw, "\n"))
        start = newline + 1
        if start >= len(text):
            return lines


def _join_lines(lines: list[tuple[str, str]], default_eol: str = "\n") -> str:
    parts: list[str] = []
    for position, (content, eol) in enumerate(lines):
        line_eol = eol
        if line_eol == "" and position < len(lines) - 1:
            # A missing-EOL line can only be the file's final line.
            line_eol = default_eol
        parts.append(content + line_eol)
    return "".join(parts)


def _detect_eol(lines: list[tuple[str, str]]) -> str:
    for _content, eol in lines:
        if eol:
            return eol
    return "\n"


# ---------------------------------------------------------------------------
# hunk application
# ---------------------------------------------------------------------------
def _reverse_hunk(hunk: _Hunk) -> _Hunk:
    swaps = {"+": "-", "-": "+", " ": " "}
    return _Hunk(
        old_start=hunk.new_start,
        old_count=hunk.new_count,
        new_start=hunk.old_start,
        new_count=hunk.old_count,
        header=f"reverse({hunk.header})",
        lines=[_HunkLine(swaps[line.op], line.content, line.no_newline) for line in hunk.lines],
    )


def _apply_hunks(
    old_lines: list[tuple[str, str]],
    hunks: list[_Hunk],
    *,
    filename: str,
    default_eol: str,
) -> list[tuple[str, str]]:
    new_lines: list[tuple[str, str]] = []
    position = 0  # next unconsumed line in ``old_lines``

    for hunk_index, hunk in enumerate(hunks, start=1):
        where = f"{filename} hunk #{hunk_index} {hunk.header}"
        # ``-0,0`` means "insert at the very beginning"; ``-5,0`` means
        # "insert after line 5" (0-based position 5).
        expected = hunk.old_start - 1 if hunk.old_count > 0 else hunk.old_start
        if expected < position:
            raise _PatchError(f"{where}: hunks are out of order or overlapping (expected line {expected + 1})")
        if expected > len(old_lines):
            raise _PatchError(
                f"{where}: patch expects line {expected + 1} but {filename} only has {len(old_lines)} lines"
            )
        new_lines.extend(old_lines[position:expected])
        position = expected
        context_count = added_count = 0

        for body_line in hunk.lines:
            if body_line.op in {" ", "-"}:
                if position >= len(old_lines):
                    raise _PatchError(
                        f"{where}: context mismatch at {filename}:{position + 1}: "
                        f"patch expects {body_line.content!r} but the file has ended"
                    )
                actual_content, actual_eol = old_lines[position]
                if actual_content != body_line.content:
                    raise _PatchError(
                        f"{where}: context mismatch at {filename}:{position + 1}: "
                        f"expected {body_line.content!r} (patch) but found {actual_content!r}"
                    )
                if body_line.no_newline and actual_eol != "":
                    raise _PatchError(
                        f"{where}: {filename}:{position + 1} is expected to have no trailing newline "
                        "but the file has one"
                    )
                if body_line.op == " ":
                    new_lines.append(old_lines[position])
                    context_count += 1
                position += 1
            else:  # "+"
                eol = "" if body_line.no_newline else default_eol
                new_lines.append((body_line.content, eol))
                added_count += 1

        if position - expected != hunk.old_count:
            raise _PatchError(f"{where}: consumed {position - expected} source lines, header says {hunk.old_count}")
        if context_count + added_count != hunk.new_count:
            raise _PatchError(
                f"{where}: produced {context_count + added_count} target lines, header says {hunk.new_count}"
            )

    new_lines.extend(old_lines[position:])
    for index, (_content, eol) in enumerate(new_lines):
        if eol == "" and index != len(new_lines) - 1:
            raise _PatchError(
                f"{filename}: a '\\ No newline at end of file' line is not the last line after applying patch"
            )
    return new_lines


# ---------------------------------------------------------------------------
# planning / execution
# ---------------------------------------------------------------------------
def _plan_section(work_dir: Path, section: _FilePatch, *, reverse: bool) -> _FilePlan:
    if reverse:
        old_path = section.new_path
        new_path = section.old_path
        hunks = [_reverse_hunk(hunk) for hunk in section.hunks]
    else:
        old_path = section.old_path
        new_path = section.new_path
        hunks = section.hunks

    display_path = new_path or old_path or "<unknown>"

    source: Path | None = None
    if old_path is not None:
        source = _resolve_target(work_dir, old_path)
        if not source.is_file():
            raise _PatchError(f"{display_path}: source file does not exist: {old_path}")
        # Read bytes + decode manually: Path.read_text() applies universal
        # newline translation and would erase CRLF endings on Windows/Linux.
        old_text = source.read_bytes().decode("utf-8", errors="surrogateescape")
    else:
        old_text = ""

    target: Path | None = None
    if new_path is not None:
        target = _resolve_target(work_dir, new_path)
        if source is None and target.exists():
            raise _PatchError(f"{display_path}: cannot create file because it already exists")

    old_lines = _split_lines(old_text)
    default_eol = _detect_eol(old_lines)
    new_lines = _apply_hunks(old_lines, hunks, filename=display_path, default_eol=default_eol)

    if target is None:
        # Deletion: the section must remove the whole file.
        if new_lines:
            raise _PatchError(
                f"{display_path}: deletion patch leaves {len(new_lines)} lines behind; refusing partial delete"
            )
        return _FilePlan(path=old_path or display_path, content=None, change="delete", hunks=len(hunks))

    content = _join_lines(new_lines, default_eol=default_eol)
    change = "create" if source is None else "modify"
    return _FilePlan(path=new_path, content=content, change=change, hunks=len(hunks))


def _error_result(message: str, *, reverse: bool) -> dict[str, Any]:
    verb = "reverse-apply" if reverse else "apply"
    return {
        "ok": False,
        "stderr": f"pure-Python unified diff {verb} failed: {message}",
        "stdout": "",
        "applied": False,
        "returncode": 1,
        "tool": "python",
    }


def _write_plan(plan: _FilePlan, work_dir: Path) -> None:
    target = _resolve_target(work_dir, plan.path)
    if plan.content is None:
        if target.exists():
            target.unlink()
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(plan.content, encoding="utf-8", errors="surrogateescape", newline="")


def _apply(
    work_dir: Path | str,
    patch_text: str,
    *,
    reverse: bool,
    check_only: bool,
) -> dict[str, Any]:
    work_path = Path(work_dir)
    if not work_path.is_dir():
        return _error_result(f"work_dir does not exist or is not a directory: {work_path}", reverse=reverse)

    patch = extract_unified_diff(patch_text)
    if patch is None:
        return _error_result("no unified diff found in patch text", reverse=reverse)

    try:
        sections = _parse_patch(patch)
        plans = [_plan_section(work_path, section, reverse=reverse) for section in sections]
        targets = [plan.path for plan in plans]
        if len(set(targets)) != len(targets):
            raise _PatchError(f"patch touches the same file more than once: {targets}")
        if not check_only:
            for plan in plans:
                _write_plan(plan, work_path)
    except _PatchError as exc:
        return _error_result(str(exc), reverse=reverse)
    except (OSError, UnicodeError) as exc:  # filesystem/encoding problems
        return _error_result(f"{type(exc).__name__}: {exc}", reverse=reverse)

    verb = "reversed" if reverse else "applied"
    summary = ", ".join(f"{plan.change} {plan.path} ({plan.hunks} hunk{'s' if plan.hunks != 1 else ''})" for plan in plans)
    return {
        "ok": True,
        "stderr": "",
        "stdout": f"{verb} {len(plans)} file(s): {summary}",
        "applied": not check_only,
        "returncode": 0,
        "tool": "python",
        "files": [
            {"path": plan.path, "change": plan.change, "hunks": plan.hunks}
            for plan in plans
        ],
    }


# ---------------------------------------------------------------------------
# public API (unchanged signature/result keys used by sddctx.cli)
# ---------------------------------------------------------------------------
def apply_patch(work_dir: Path | str, patch_text: str, *, check_only: bool = False) -> dict[str, Any]:
    """Apply ``patch_text`` inside ``work_dir`` with the built-in engine.

    Returns
    -------
    dict
        ``{"ok", "stderr", "stdout", "applied", "returncode", "tool"}``.
        ``ok`` is true only when every file section applied cleanly (exact
        context); ``applied`` is false for ``check_only=True``.  ``tool`` is
        always ``"python"`` — there is no external ``patch``/``git`` fallback.
    """

    return _apply(work_dir, patch_text, reverse=False, check_only=check_only)


def reverse_patch(
    work_dir: Path | str,
    patch_text: str,
    *,
    check_only: bool = False,
) -> dict[str, Any]:
    """Reverse-apply ``patch_text`` inside ``work_dir`` (built-in engine).

    Used by the demo flow to restore the pre-patch state while keeping the
    after-state in evidence.  With ``check_only=True`` the tree is only
    inspected and never modified — ``sddctx.cli`` uses this to tell whether
    the demo is currently in the before or after state.
    """

    return _apply(work_dir, patch_text, reverse=True, check_only=check_only)
