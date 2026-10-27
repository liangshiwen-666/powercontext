"""Shared data contracts for the SDD context toolkit.

These ``TypedDict`` definitions are the contract between the ingestion layer,
the seekdb material store, the context assembler and the CLI.  Keep them in
sync when extending the toolkit.
"""

from __future__ import annotations

from typing import Literal, TypedDict

DocKind = Literal["spec", "code", "test"]


class Chunk(TypedDict):
    """One addressable slice of engineering material."""

    kind: DocKind
    path: str  # POSIX path relative to the ``minimax/`` root, e.g. ``demo/app/service.py``
    line_start: int  # 1-based, inclusive
    line_end: int  # 1-based, inclusive
    title: str
    symbol: str
    body: str


class SearchHit(TypedDict):
    """A retrieval hit enriched with the citation used in the context pack."""

    kind: DocKind
    path: str
    line_start: int
    line_end: int
    title: str
    symbol: str
    score: float
    snippet: str
    citation: str


class StateEntry(TypedDict):
    """One persisted task-state record read back from PowerContext."""

    kind: str
    text: str
    ref: dict
    state: str
