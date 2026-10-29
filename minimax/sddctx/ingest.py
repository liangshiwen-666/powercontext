"""Turn the demo engineering material into seekdb chunks.

Chunking rules (see task requirements):

* ``spec`` — every ``### REQ-...`` section of the markdown files directly
  under ``demo/specs`` (or an explicit ``specs`` directory).  The directory is
  scanned **non-recursively**: ``demo/specs/history/orders.v1.md`` is a
  superseded revision and must not be ingested by the default run (otherwise
  the context pack would mix v1 and v2 requirements).  Pass
  ``specs=demo/specs/history`` to import that revision explicitly.
  ``symbol`` is the requirement id (``REQ-ORD-006``), ``title`` the heading.
* ``code`` — every **top-level** ``def``/``class`` in ``demo/app/**/*.py``.
  ``body`` is the exact source slice (decorators included) and the line range
  is 1-based and inclusive.
* ``test`` — every top-level ``test_*`` function in ``demo/tests/**/*.py``.
  ``title`` is the first docstring line when present, else the function name.

All ``path`` values are POSIX paths relative to the ``minimax/`` root
(``demo/app/service.py``), which is the same form used by the citation
(``path:line_start-line_end``) returned by :class:`sddctx.store.MaterialStore`.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ToolkitConfig
from .protocols import Chunk
from .store import DOC_KINDS, MaterialStore

#: ``### REQ-ORD-006 状态筛选（v2.0 新增）``
_REQ_HEADING = re.compile(r"^###\s+(REQ-[A-Z][A-Z0-9]*-\d{3,})\b[ \t]*(.*)$")
#: Any heading of level <= 3 terminates a requirement section.
_SECTION_BREAK = re.compile(r"^#{2,3}\s+")
_TOP_LEVEL_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


# ---------------------------------------------------------------------------
# path helpers
# ---------------------------------------------------------------------------
def minimax_root(demo_root: Path) -> Path:
    """Return the ``minimax/`` root for a demo tree.

    ``demo_root`` is ``<minimax>/demo`` in normal use, but accepting an
    arbitrary directory keeps the helper usable from tests and ad-hoc scripts.
    """

    demo = Path(demo_root).resolve()
    return demo.parent if demo.name == "demo" else demo


def _relpath(path: Path, root: Path) -> str:
    """POSIX path relative to ``root`` (absolute fallback outside the tree)."""

    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return resolved.as_posix()


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _slice_body(lines: Sequence[str], line_start: int, line_end: int) -> str:
    """Return the exact source slice for an inclusive 1-based line range."""

    return "\n".join(lines[line_start - 1 : line_end])


def _trim_trailing_blank(lines: Sequence[str], start_index: int, end_index: int) -> int:
    """Trim blank lines before ``end_index`` (exclusive) but keep the heading."""

    end = end_index
    while end > start_index + 1 and not lines[end - 1].strip():
        end -= 1
    return end


# ---------------------------------------------------------------------------
# chunk extraction
# ---------------------------------------------------------------------------
def collect_chunks(demo_root: Path, *, specs: Path | None = None) -> list[Chunk]:
    """Collect spec, code and test chunks from ``demo_root``.

    Parameters
    ----------
    demo_root:
        The business project root (``<minimax>/demo``).
    specs:
        Optional override for the specs directory.  Defaults to
        ``demo_root/specs``.  Only ``*.md`` files directly inside the given
        directory are read (never sub-directories such as ``history/``).
    """

    demo = Path(demo_root).resolve()
    root = minimax_root(demo)
    specs_dir = Path(specs).resolve() if specs is not None else demo / "specs"

    chunks: list[Chunk] = []
    chunks.extend(_spec_chunks(specs_dir, root))
    chunks.extend(_code_chunks(demo / "app", root))
    chunks.extend(_test_chunks(demo / "tests", root))
    return chunks


def _spec_chunks(specs_dir: Path, root: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    if not specs_dir.exists():
        return chunks

    for path in sorted(p for p in specs_dir.glob("*.md") if p.is_file()):
        # Non-recursive on purpose: ``specs/history/*.md`` holds superseded
        # revisions that must not pollute the default context pack.
        try:
            lines = _read_lines(path)
        except OSError:
            continue
        if not lines:
            continue
        rel = _relpath(path, root)

        headings: list[tuple[int, str, str]] = []  # (0-based line index, symbol, title)
        for index, line in enumerate(lines):
            match = _REQ_HEADING.match(line)
            if match:
                headings.append((index, match.group(1), match.group(2).strip()))

        if not headings:
            # Keep the file searchable even when it is not organised in REQ
            # sections (e.g. an ad-hoc notes file).
            start_index = next((i for i, line in enumerate(lines) if line.strip()), 0)
            end_index = _trim_trailing_blank(lines, start_index, len(lines))
            chunks.append(
                Chunk(
                    kind="spec",
                    path=rel,
                    line_start=start_index + 1,
                    line_end=end_index,
                    title=_first_heading_text(lines) or path.stem,
                    symbol="",
                    body=_slice_body(lines, start_index + 1, end_index),
                )
            )
            continue

        for _position, (start_index, symbol, title) in enumerate(headings):
            end_index = len(lines)
            for probe in range(start_index + 1, len(lines)):
                if _SECTION_BREAK.match(lines[probe]):
                    end_index = probe
                    break
            end_index = _trim_trailing_blank(lines, start_index, end_index)
            chunks.append(
                Chunk(
                    kind="spec",
                    path=rel,
                    line_start=start_index + 1,
                    line_end=end_index,
                    title=title or symbol,
                    symbol=symbol,
                    body=_slice_body(lines, start_index + 1, end_index),
                )
            )
    return chunks


def _first_heading_text(lines: Sequence[str]) -> str:
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def _symbol_start(node: ast.AST) -> int:
    start = int(getattr(node, "lineno", 1))
    for decorator in getattr(node, "decorator_list", []):  # type: ignore[attr-defined]
        start = min(start, int(getattr(decorator, "lineno", start)))
    return start


def _code_chunks(app_dir: Path, root: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    if not app_dir.exists():
        return chunks

    for path in sorted(p for p in app_dir.rglob("*.py") if p.is_file()):
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        lines = source.splitlines()
        rel = _relpath(path, root)
        for node in tree.body:
            if not isinstance(node, _TOP_LEVEL_NODES):
                continue
            line_start = _symbol_start(node)
            line_end = int(getattr(node, "end_lineno", line_start))
            if line_end < line_start:
                continue
            name = str(node.name)
            chunks.append(
                Chunk(
                    kind="code",
                    path=rel,
                    line_start=line_start,
                    line_end=line_end,
                    title=name,
                    symbol=name,
                    body=_slice_body(lines, line_start, line_end),
                )
            )
    return chunks


def _test_chunks(tests_dir: Path, root: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    if not tests_dir.exists():
        return chunks

    for path in sorted(p for p in tests_dir.rglob("*.py") if p.is_file()):
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        lines = source.splitlines()
        rel = _relpath(path, root)
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = str(node.name)
            if not name.startswith("test_"):
                continue
            line_start = _symbol_start(node)
            line_end = int(getattr(node, "end_lineno", line_start))
            if line_end < line_start:
                continue
            docstring = ast.get_docstring(node) or ""
            title = docstring.strip().splitlines()[0].strip() if docstring.strip() else name
            chunks.append(
                Chunk(
                    kind="test",
                    path=rel,
                    line_start=line_start,
                    line_end=line_end,
                    title=title,
                    symbol=name,
                    body=_slice_body(lines, line_start, line_end),
                )
            )
    return chunks


# ---------------------------------------------------------------------------
# ingestion
# ---------------------------------------------------------------------------
def _counts_by_kind(chunks: Sequence[Chunk]) -> dict[str, int]:
    counts: dict[str, int] = {str(kind): 0 for kind in DOC_KINDS}
    for chunk in chunks:
        kind = str(chunk["kind"])
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _archive_path(archive_dir: Path | None) -> Path:
    """Return ``<archive_dir>/ingest-<UTC>.jsonl``.

    ``archive_dir`` is the directory that holds the snapshots; callers such as
    the CLI pass ``data_dir/ingest``.  When omitted it defaults to
    ``<data_dir>/ingest`` (``SDDCTX_DATA`` aware).  The timestamp includes
    microseconds so two ingests in the same second never overwrite each
    other's snapshot.
    """

    if archive_dir is not None:
        target = Path(archive_dir).expanduser().resolve()
    else:
        target = ToolkitConfig.from_env().data_dir / "ingest"
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return target / f"ingest-{stamp}.jsonl"


def ingest(
    store: MaterialStore,
    demo_root: Path,
    *,
    specs: Path | None = None,
    reset_kinds: Sequence[str] = ("spec", "code", "test"),
    archive_dir: Path | None = None,
) -> dict[str, Any]:
    """Reset the selected kinds, upsert all chunks and snapshot them as JSONL.

    Returns ``{"chunks": <rows written>, "counts": {kind: n}, "archive": <path>}``.
    The JSONL file contains one ``Chunk`` per line and uses UTF-8 without
    escaping non-ASCII text, so spec/code/tests remain readable.
    """

    chunks = collect_chunks(demo_root, specs=specs)

    store.init_schema()
    if reset_kinds:
        store.reset(list(reset_kinds))

    written = store.upsert(chunks)

    archive = _archive_path(archive_dir)
    with archive.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(dict(chunk), ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    counts = _counts_by_kind(chunks)
    return {"chunks": written, "counts": counts, "archive": str(archive)}
