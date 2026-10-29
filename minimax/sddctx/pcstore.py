"""Durable task state backed by an in-process PowerContext runtime.

This module wraps ``powercontext.builtin`` (PowerContext 1.4.1, ``powercontext[builtin]``
extra) so the toolkit can persist task progress, decisions and final outcomes without
starting a PowerContext Server, opening a port, or setting any ``POWERCONTEXT_*``
environment variable.

Design notes (each one is a measured fact, not a guess):

* The SQLite database must be file backed. ``BuiltinConfig()`` defaults to
  ``sqlite+aiosqlite:///:memory:`` and silently loses every record when the runtime
  closes, so ``TaskStateStore`` always builds an explicit absolute file URL.
* A Scope must exist before any Memory write. Creation is idempotent through
  ``ScopeDraft.idempotency_key``: the same key with the same title/summary returns the
  same ``scope_id`` on every call, which is what makes a stable ``scope_key`` survive
  process restarts. Reusing a key with different parameters raises
  ``ScopeIdempotencyConflictError`` and is surfaced as :class:`ScopeConflictError`.
* Atomic Memory replaced the legacy ``memory`` family in 1.4.1. A memory is created with
  :class:`AtomicMemoryContent`; search accepts ``mode="text"`` (FTS) which needs no
  embedding model.
* One Memory text value is capped at 8192 UTF-8 bytes, so long values are split before
  they are written. Caller text is normalized the way PowerContext stores it (Unicode
  NFC plus ``strip()``) before splitting, and the splitter never cuts next to
  whitespace or inside a combining sequence, so ``"".join(save(text))`` equals
  ``NFC(text).strip()`` byte for byte.
* The Handoff chain (work contract -> current-work boundary -> prepared handoff ->
  commit -> continue -> acknowledge -> task outcome -> continuity) is deterministic and
  needs no generation model.

Every public method is synchronous: it opens the runtime, performs one operation and
closes the runtime again. Opening a warm database costs roughly 0.1-0.25 s, which is
cheap enough for a CLI invocation and keeps no background process alive between calls.
"""

from __future__ import annotations

import asyncio
import os
import random
import threading
import time
import unicodedata
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, Awaitable, Callable, Literal, Sequence

__all__ = [
    "DEFAULT_SCOPE_KEY",
    "MAX_TEXT_BYTES",
    "PcStoreError",
    "PcStoreUnavailableError",
    "ScopeConflictError",
    "TaskOutcomeStatus",
    "TaskStateStore",
    "split_text",
]

MAX_TEXT_BYTES = 8192
"""PowerContext rejects one Memory text value above 8192 UTF-8 bytes."""

DEFAULT_SCOPE_KEY = "sddctx-orders-v1"
"""Default idempotency key for the toolkit's task-state Scope."""

SCOPE_TITLE = "sddctx task state"
SCOPE_SUMMARY = "Spec-driven development toolkit task state (in-process PowerContext)"

MAX_LIST_LIMIT = 100
"""PowerContext accepts ``1 <= limit <= 100`` for Memory list and search."""

MAX_WORK_ITEMS = 64
"""Work contracts, task outcomes and continuity project at most 64 items."""

MAX_OPERATION_RETRIES = 4
"""Retries for transient PowerContext bootstrap/write races (fresh database, WAL lock)."""

_RETRY_DELAYS = (0.25, 0.5, 1.0, 2.0)
"""Backoff between transient retries; 0.25 + 0.5 + 1.0 + 2.0 = 3.75 s of patience."""

_TRANSIENT_MARKERS = (
    "plan_changed",
    "initialization baseline changed",
    "database is locked",
    "database table is locked",
    "database schema is locked",
    # A concurrent bootstrap can observe the winner's DDL halfway through: the second
    # process may try to create a table the winner just committed ("already exists"),
    # query a table that is not visible yet ("no such table"), or see a partially
    # initialized database ("unknown_baseline", "partial database"). A fresh open after
    # the winner finishes resolves all of them.
    "already exists",
    "no such table",
    "duplicate column name",
    "unknown_baseline",
    "partial database",
)
"""Substrings of PowerContext/SQLite errors that a retry can resolve.

Two CLI processes that open the *same brand-new* database at the same moment race the
schema bootstrap: the loser gets ``MigrationError: plan_changed`` or a raw SQLite DDL /
partial-state error (``table ... already exists``, ``unknown_baseline``). A fresh open
after the winner committed the baseline succeeds, so the operation is retried instead of
failing.
"""

BUSY_TIMEOUT_MS = 15_000
"""SQLite writer patience; the first-open schema bootstrap can take a few seconds."""

TaskOutcomeStatus = Literal["succeeded", "failed", "partial"]

#: Handoff/family statuses accepted by ``TaskCheck``; anything else is recorded as ``unknown``.
_CHECK_STATUSES = frozenset(
    {"passed", "failed", "skipped", "timed_out", "unavailable", "cancelled", "unknown"}
)


class PcStoreError(RuntimeError):
    """Base error for task-state storage problems."""


class PcStoreUnavailableError(PcStoreError):
    """PowerContext is not installed, or the runtime could not be opened.

    The original exception is kept as ``__cause__`` so callers can inspect the real
    failure (locked database, unwritable directory, schema mismatch, ...).
    """


class ScopeConflictError(PcStoreError):
    """The scope key was already used with different scope parameters."""


# ---------------------------------------------------------------------------
# text helpers
# ---------------------------------------------------------------------------


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _fit_prefix(text: str, limit: int) -> int:
    """Return the largest character index whose UTF-8 prefix stays within ``limit`` bytes."""

    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if _utf8_len(text[:middle]) <= limit:
            low = middle
        else:
            high = middle - 1
    return low


def split_text(text: str, limit: int = MAX_TEXT_BYTES) -> list[str]:
    """Split ``text`` into pieces of at most ``limit`` UTF-8 bytes.

    ``"".join(split_text(text))`` always equals ``text``. Boundaries never fall next to
    whitespace or inside a combining sequence, because PowerContext stores each value
    with Unicode NFC plus ``strip()`` and such boundaries would change the stored text.
    Callers that need an exact round trip should pass NFC-normalized text (``save`` does
    this itself).
    """

    if limit < 1:
        raise ValueError("split limit must be positive")
    if not isinstance(text, str) or not text:
        raise ValueError("text to split must be a non-empty string")
    if _utf8_len(text) <= limit:
        return [text]

    pieces: list[str] = []
    remaining = text
    while _utf8_len(remaining) > limit:
        cut = _avoid_whitespace_boundary(remaining, _fit_prefix(remaining, limit))
        pieces.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        pieces.append(remaining)
    return pieces


def _avoid_whitespace_boundary(text: str, cut: int, window: int = 256) -> int:
    """Move ``cut`` left until the boundary is safe to split.

    A safe boundary is not next to whitespace (PowerContext strips each stored value) and
    does not separate a base character from a combining mark (which would change the NFC
    form and could mangle the text).
    """

    floor = max(1, cut - window)
    for candidate in range(cut, floor - 1, -1):
        left = text[candidate - 1]
        right = text[candidate] if candidate < len(text) else ""
        if not left.isspace() and not right.isspace() and not unicodedata.combining(right):
            return candidate
    # No safe spot inside the window: at least never split a combining sequence.
    candidate = cut
    while candidate > 1 and unicodedata.combining(text[candidate]):
        candidate -= 1
    return candidate if candidate > 0 else cut


def _require_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _normalize_stored(text: str) -> str:
    """Mirror PowerContext's stored-text normalization (Unicode NFC plus strip)."""

    return unicodedata.normalize("NFC", text).strip()


def _clean_text(value: str, field: str) -> str:
    """Validate one caller-supplied text value and normalize it the way PowerContext does."""

    cleaned = _normalize_stored(_require_text(value, field))
    if not cleaned:
        raise ValueError(f"{field} must contain non-whitespace text")
    return cleaned


def _text_items(values: Sequence[str], field: str, *, limit: int = MAX_WORK_ITEMS) -> tuple[list[str], int]:
    """Validate, normalize, split and cap a sequence of text items.

    Returns the kept pieces (each within ``MAX_TEXT_BYTES``) and the number of pieces
    dropped because of the item-count ceiling.
    """

    pieces: list[str] = []
    dropped = 0
    for index, raw in enumerate(values):
        text = _clean_text(raw, f"{field}[{index}]")
        for piece in split_text(text):
            if len(pieces) < limit:
                pieces.append(piece)
            else:
                dropped += 1
    return pieces, dropped


def _cap_texts(
    values: list[str], label: str, dropped: dict[str, int], *, limit: int = MAX_WORK_ITEMS
) -> list[str]:
    """Clamp an already-split list to the Work item ceiling, recording what was dropped."""

    if len(values) <= limit:
        return values
    dropped[label] = dropped.get(label, 0) + (len(values) - limit)
    return values[:limit]


def _order_saved(records: list[dict[str, Any]], pairs: Sequence[tuple[str, str]]) -> list[dict[str, Any]]:
    """Restore write order for ``save`` results.

    PowerContext returns the records of a batch in artifact-id order, not input order.
    The input pieces are the natural order callers expect, so match them back by
    normalized ``(kind, text)`` and append anything that did not match.
    """

    remaining = list(records)
    ordered: list[dict[str, Any]] = []
    for kind, text in pairs:
        wanted_kind = _normalize_stored(kind)
        wanted_text = _normalize_stored(text)
        for index, record in enumerate(remaining):
            if (
                _normalize_stored(record["kind"]) == wanted_kind
                and _normalize_stored(record["text"]) == wanted_text
            ):
                ordered.append(remaining.pop(index))
                break
    ordered.extend(remaining)
    return ordered


# ---------------------------------------------------------------------------
# powercontext imports (lazy so importing sddctx.pcstore works without the package)
# ---------------------------------------------------------------------------

_MODULES: SimpleNamespace | None = None
_RUN_LOCK = threading.RLock()
_LOOP: asyncio.AbstractEventLoop | None = None
_LOOP_PID: int | None = None


def _shared_loop() -> asyncio.AbstractEventLoop:
    """Return this module's long-lived event loop, creating it on first use.

    The loop is recreated when it was closed or when the process forked: a loop inherited
    across ``fork`` belongs to the parent and cannot be reused safely.
    """

    global _LOOP, _LOOP_PID
    pid = os.getpid()
    if _LOOP is None or _LOOP.is_closed() or _LOOP_PID != pid:
        _LOOP = asyncio.new_event_loop()
        _LOOP_PID = pid
    return _LOOP


def _is_transient(error: BaseException) -> bool:
    """Return whether a failure is a retryable PowerContext bootstrap/SQLite race."""

    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current)
        if any(marker in message for marker in _TRANSIENT_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False

_INSTALL_HINT = (
    "PowerContext 1.4.1 is required for sddctx.pcstore. Install it with:\n"
    '    pip install "powercontext[builtin]==1.4.1"\n'
    "and keep the package pinned: the in-process runtime API is not part of the HTTP contract."
)


def _pc() -> SimpleNamespace:
    """Import the PowerContext pieces once, with an actionable error when missing."""

    global _MODULES
    if _MODULES is not None:
        return _MODULES

    try:
        from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
        from powercontext.builtin.persistence.sqlite import SQLiteConfig
        from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime
        from powercontext.builtin.runtime.models import PrepareContextRequest
        from powercontext.builtin.scope import ScopeDraft, ScopeIdempotencyConflictError
        from powercontext.builtin.work import (
            AcknowledgeHandoff,
            CreateWorkContract,
            CurrentWorkHandoff,
            HandoffCurrentWork,
            ReceiverChecks,
            RecordTaskOutcome,
            TaskCheck,
            TaskOutcome,
            WorkClaim,
            WorkContract,
        )
    except ImportError as exc:  # pragma: no cover - exercised only on broken installs
        raise PcStoreUnavailableError(f"{_INSTALL_HINT}\nOriginal import error: {exc}") from exc

    _MODULES = SimpleNamespace(
        AtomicMemoryContent=AtomicMemoryContent,
        SQLiteConfig=SQLiteConfig,
        BuiltinConfig=BuiltinConfig,
        open_builtin_runtime=open_builtin_runtime,
        PrepareContextRequest=PrepareContextRequest,
        ScopeDraft=ScopeDraft,
        ScopeIdempotencyConflictError=ScopeIdempotencyConflictError,
        AcknowledgeHandoff=AcknowledgeHandoff,
        CreateWorkContract=CreateWorkContract,
        CurrentWorkHandoff=CurrentWorkHandoff,
        HandoffCurrentWork=HandoffCurrentWork,
        ReceiverChecks=ReceiverChecks,
        RecordTaskOutcome=RecordTaskOutcome,
        TaskCheck=TaskCheck,
        TaskOutcome=TaskOutcome,
        WorkClaim=WorkClaim,
        WorkContract=WorkContract,
    )
    return _MODULES


# ---------------------------------------------------------------------------
# TaskStateStore
# ---------------------------------------------------------------------------


class TaskStateStore:
    """Persist task state in one PowerContext Scope inside one SQLite file.

    Parameters
    ----------
    db_path:
        SQLite database file. Parent directories are created when missing and the path
        is resolved to an absolute path so the runtime always opens a file database.
    scope_key:
        Stable idempotency key for the Scope. Keep it constant across runs: the same key
        resolves to the same ``scope_id`` for the lifetime of the database.
    """

    def __init__(self, db_path: str | Path, scope_key: str = DEFAULT_SCOPE_KEY) -> None:
        path = Path(db_path).expanduser()
        if not str(path).strip():
            raise ValueError("db_path must not be empty")
        self.db_path = path.resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.scope_key = _require_text(scope_key, "scope_key")
        # SQLAlchemy URL: four slashes for an absolute POSIX path, e.g.
        # sqlite+aiosqlite:////mnt/d/work/.data/powercontext/pc.db
        self._db_url = f"sqlite+aiosqlite:///{self.db_path.as_posix()}"
        # Keep scheduler state next to the database instead of the process CWD.
        self._scheduler_path = self.db_path.with_name(f"{self.db_path.stem}.scheduler.db")

    # -- public API ------------------------------------------------------

    def scope_id(self) -> str:
        """Return the durable Scope id for :attr:`scope_key` (creating it if needed)."""

        return self._run(lambda: self._a_scope_id())

    def save(self, entries: Sequence[tuple[str, str]]) -> list[dict[str, Any]]:
        """Write ``(kind, text)`` entries as Atomic Memories.

        Text is normalized exactly like PowerContext stores it (Unicode NFC, leading and
        trailing whitespace stripped) and then split into 8192-byte pieces, so one logical
        entry can yield several records. ``"".join(record["text"] for record in result)``
        is ``NFC(entry_text).strip()`` byte for byte. Returns one ``{kind, text, ref,
        state}`` dict per created record, in write order.
        """

        pairs: list[tuple[str, str]] = []
        for index, entry in enumerate(entries):
            try:
                kind, text = entry
            except (TypeError, ValueError) as exc:
                raise ValueError(f"entries[{index}] must be a (kind, text) pair") from exc
            kind_text = _clean_text(kind, f"entries[{index}].kind")
            if len(kind_text) > 128:
                raise ValueError(f"entries[{index}].kind must not exceed 128 characters")
            body = _clean_text(text, f"entries[{index}].text")
            pairs.extend((kind_text, piece) for piece in split_text(body))
        if not pairs:
            return []
        return self._run(lambda: self._a_save(pairs))

    def list(self) -> list[dict[str, Any]]:
        """Return every active Memory record in this Scope.

        The runtime orders records by artifact id (not by write time); callers that need
        a specific presentation order should sort the result themselves.
        """

        return self._run(lambda: self._a_list())

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Full-text search the Scope (``mode="text"``, no embedding model required)."""

        text = _require_text(query, "query")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIST_LIMIT:
            raise ValueError(f"limit must be an integer between 1 and {MAX_LIST_LIMIT}")
        return self._run(lambda: self._a_search(text, limit))

    def prepare(self, query: str, max_bytes: int = 6000) -> dict[str, Any]:
        """Return bounded context for one prompt.

        The result mirrors PowerContext: ``{"status": "ready"|"empty", "content": str|None,
        "bytes": int}``. Ready content is a self-describing envelope where every item
        carries its exact ``artifact_ref`` citation.
        """

        text = _require_text(query, "query")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 512 <= max_bytes <= 32768:
            raise ValueError("max_bytes must be an integer between 512 and 32768")
        return self._run(lambda: self._a_prepare(text, max_bytes))

    def record_task_outcome(
        self,
        *,
        objective: str,
        status: TaskOutcomeStatus,
        summary: str,
        checks: Sequence[tuple[str, str]],
        done: Sequence[str],
        todo: Sequence[str],
    ) -> dict[str, Any]:
        """Record the deterministic work loop for one finished task attempt.

        The chain is ``create_work_contract -> handoff_current -> commit ->
        continue_from -> acknowledge -> record_task_outcome -> continuity``. It needs no
        generation model: every value is caller-authored. Returns the exact references
        (contract source, committed handoff artifact, acknowledgement receipt, task
        outcome source) plus the projected continuity timeline.
        """

        if status not in ("succeeded", "failed", "partial"):
            raise ValueError('status must be "succeeded", "failed" or "partial"')

        objective_pieces = split_text(_clean_text(objective, "objective"))
        done_pieces, done_dropped = _text_items(done, "done")
        todo_pieces, todo_dropped = _text_items(todo, "todo")
        summary_pieces = split_text(_clean_text(summary, "summary"))

        normalized_checks: list[tuple[str, str]] = []
        dropped_checks = 0
        for index, check in enumerate(checks):
            try:
                name, check_status = check
            except (TypeError, ValueError) as exc:
                raise ValueError(f"checks[{index}] must be a (name, status) pair") from exc
            clean_name = _clean_text(name, f"checks[{index}].name")
            clean_status = str(check_status).strip().lower()
            if clean_status not in _CHECK_STATUSES:
                clean_status = "unknown"
            for piece in split_text(clean_name):
                if len(normalized_checks) < MAX_WORK_ITEMS:
                    normalized_checks.append((piece, clean_status))
                else:
                    dropped_checks += 1

        request = SimpleNamespace(
            # first piece stays the headline (bounded field), the rest keeps the evidence
            objective=objective_pieces[0],
            objective_rest=objective_pieces[1:],
            status=cast_status(status),
            summary=summary_pieces[0],
            summary_rest=summary_pieces[1:],
            checks=tuple(normalized_checks),
            done=tuple(done_pieces),
            todo=tuple(todo_pieces),
            dropped={"done": done_dropped, "todo": todo_dropped, "checks": dropped_checks},
        )
        return self._run(lambda: self._a_record_task_outcome(request))

    # -- async implementation -------------------------------------------

    def _run(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        """Run one operation on a long-lived private loop.

        Each method opens and closes a real runtime, so the loop must outlive the
        aiosqlite worker threads that PowerContext's SQLite profile starts; closing the
        loop after every call races those threads ("Event loop is closed"). A private
        loop owned by this module avoids that race and keeps the call synchronous.

        Transient failures (two processes bootstrapping the same fresh database, SQLite
        writer locks) are retried with backoff. Any other unexpected failure is wrapped in
        :class:`PcStoreError` with the original exception chained, so a CLI reports one
        actionable error instead of a raw sqlite3/PowerContext traceback.
        """

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise PcStoreError(
                "TaskStateStore methods are synchronous and cannot be called from a running "
                "event loop; call them from a worker thread (asyncio.to_thread) instead."
            )

        last_error: Exception | None = None
        for attempt in range(MAX_OPERATION_RETRIES + 1):
            if attempt:
                # Jitter keeps two racing processes from retrying in lockstep and
                # colliding on the same bootstrap window again.
                time.sleep(_RETRY_DELAYS[attempt - 1] * random.uniform(0.75, 1.25))
            try:
                with _RUN_LOCK:
                    return _shared_loop().run_until_complete(operation())
            except Exception as exc:
                if isinstance(exc, (PcStoreError, ValueError)):
                    if _is_transient(exc) and attempt < MAX_OPERATION_RETRIES:
                        last_error = exc
                        continue
                    raise
                if _is_transient(exc) and attempt < MAX_OPERATION_RETRIES:
                    last_error = exc
                    continue
                raise PcStoreError(
                    f"PowerContext task-state operation failed for {self.db_path}: {exc}"
                ) from exc
        raise PcStoreError(
            f"PowerContext task-state operation kept failing for {self.db_path} after "
            f"{MAX_OPERATION_RETRIES + 1} attempts"
        ) from last_error

    @asynccontextmanager
    async def _runtime(self) -> AsyncIterator[Any]:
        modules = _pc()
        config = modules.BuiltinConfig(
            database=modules.SQLiteConfig(url=self._db_url, busy_timeout_ms=BUSY_TIMEOUT_MS),
        )
        manager = modules.open_builtin_runtime(config, scheduler_path=str(self._scheduler_path))
        try:
            runtime = await manager.__aenter__()
        except Exception as exc:
            raise PcStoreUnavailableError(
                f"failed to open PowerContext runtime for database {self.db_path}: {exc}"
            ) from exc
        try:
            yield runtime
        finally:
            await manager.__aexit__(None, None, None)

    async def _scope(self, runtime: Any) -> str:
        """Create the Scope idempotently and return its durable id."""

        modules = _pc()
        draft = modules.ScopeDraft(
            title=SCOPE_TITLE,
            summary=SCOPE_SUMMARY,
            idempotency_key=self.scope_key,
        )
        try:
            scope = await runtime.scopes.create(draft)
        except modules.ScopeIdempotencyConflictError as exc:
            raise ScopeConflictError(
                f"scope key {self.scope_key!r} was already used with different scope "
                f"parameters in {self.db_path}; keep the title/summary stable or use a new "
                "scope_key"
            ) from exc
        except Exception as exc:
            raise PcStoreError(f"failed to create PowerContext scope: {exc}") from exc
        return scope.scope_id

    async def _a_scope_id(self) -> str:
        async with self._runtime() as runtime:
            return await self._scope(runtime)

    async def _a_save(self, pairs: Sequence[tuple[str, str]]) -> list[dict[str, Any]]:
        modules = _pc()
        async with self._runtime() as runtime:
            scope_id = await self._scope(runtime)
            memory = runtime.atomic_memory.for_scope(scope_id)
            result = await memory.create(
                tuple(modules.AtomicMemoryContent(kind=kind, text=text) for kind, text in pairs)
            )
            return _order_saved([_record(record) for record in result.records], pairs)

    async def _a_list(self) -> list[dict[str, Any]]:
        async with self._runtime() as runtime:
            scope_id = await self._scope(runtime)
            memory = runtime.atomic_memory.for_scope(scope_id)
            records: list[dict[str, Any]] = []
            cursor: str | None = None
            while True:
                page = await memory.list(limit=MAX_LIST_LIMIT, cursor=cursor)
                records.extend(_record(record) for record in page.items)
                cursor = page.next_cursor
                if cursor is None:
                    break
            return records

    async def _a_search(self, query: str, limit: int) -> list[dict[str, Any]]:
        async with self._runtime() as runtime:
            scope_id = await self._scope(runtime)
            page = await runtime.atomic_memory.for_scope(scope_id).search(
                query, mode="text", limit=limit
            )
            return [
                {
                    "kind": hit.hit.kind,
                    "text": hit.hit.text,
                    "ref": hit.hit.artifact_ref.model_dump(mode="json"),
                    "score": float(hit.hit.score),
                }
                for hit in page.hits
            ]

    async def _a_prepare(self, query: str, max_bytes: int) -> dict[str, Any]:
        modules = _pc()
        async with self._runtime() as runtime:
            scope_id = await self._scope(runtime)
            prepared = await runtime.context.for_scope(scope_id).prepare(
                modules.PrepareContextRequest(query=query, max_bytes=max_bytes)
            )
            return {
                "status": str(prepared.status),
                "content": prepared.content,
                "bytes": prepared.content_bytes,
            }

    async def _a_record_task_outcome(self, request: Any) -> dict[str, Any]:
        modules = _pc()
        async with self._runtime() as runtime:
            scope_id = await self._scope(runtime)
            stamp = time.time_ns()
            dropped = request.dropped

            # 1. Work contract: objective, scope and completion boundary as Source evidence.
            in_scope = list(request.done) or list(request.todo) or ["task attempt recorded by sddctx.pcstore"]
            completion = list(request.done) or ["(no completed item recorded)"]
            contract_receipt = await runtime.work.for_scope(scope_id).create_contract(
                modules.CreateWorkContract(
                    source_id=f"pcstore-contract-{stamp}",
                    contract=modules.WorkContract(
                        objective=request.objective,
                        in_scope=tuple(in_scope),
                        completion_criteria=tuple(completion),
                        authorization_notes=("Recorded by sddctx.pcstore.TaskStateStore",),
                    ),
                )
            )

            # 2. Current-work boundary: caller-inspected state -> temporary Handoff.
            state_claims = list(request.done) or [request.summary]
            state_claims.extend(request.objective_rest)
            state_claims.extend(request.summary_rest)
            state_claims = _cap_texts(state_claims, "state claims", dropped)
            disposition = (
                "complete"
                if request.status == "succeeded" and not request.todo
                else "blocked" if request.status == "failed" else "continuable"
            )
            boundary = await runtime.work.for_scope(scope_id).handoff_current(
                modules.HandoffCurrentWork(
                    source_id=f"pcstore-boundary-{stamp}",
                    handoff=modules.CurrentWorkHandoff(
                        objective=request.objective,
                        state=tuple(modules.WorkClaim(text=text) for text in state_claims),
                        disposition=disposition,
                        next_action=(
                            modules.WorkClaim(text=request.todo[0]) if request.todo else None
                        ),
                        omissions=(
                            (
                                f"{dropped['done'] + dropped['todo'] + dropped['checks']} item(s) "
                                "omitted because the per-kind limit of 64 was reached",
                            )
                            if any(dropped.values())
                            else ()
                        ),
                    ),
                )
            )

            # 3. Commit the milestone, then resolve it exactly (no generation model involved).
            committed = await runtime.handoff.for_scope(scope_id).commit(boundary.handoff)
            handoff_ref = committed.as_ref()
            resolution = await runtime.handoff.for_scope(scope_id).continue_from(handoff_ref)

            # 4. Receiver acknowledgement: accepted requires all three live checks confirmed.
            acknowledgement = await runtime.work.for_scope(scope_id).acknowledge(
                modules.AcknowledgeHandoff(
                    source_id=f"pcstore-ack-{stamp}",
                    receiver="sddctx-pcstore",
                    status="accepted",
                    selection="exact",
                    revision=handoff_ref,
                    receiver_checks=modules.ReceiverChecks(
                        live_state="confirmed",
                        capability="confirmed",
                        authorization="confirmed",
                    ),
                )
            )

            # 5. Task outcome: status, checks and the accepted receipt link.
            observations = [request.summary, *request.done, *request.objective_rest, *request.summary_rest]
            observations = observations or ["(no observation recorded)"]
            observations = _cap_texts(observations, "observations", dropped)
            outcome_receipt = await runtime.work.for_scope(scope_id).record_outcome(
                modules.RecordTaskOutcome(
                    source_id=f"pcstore-outcome-{stamp}",
                    outcome=modules.TaskOutcome(
                        objective=request.objective,
                        status=request.status,
                        summary=request.summary,
                        handoff_receipt_ref=acknowledgement.receipt.source_ref,
                        observations=tuple(modules.WorkClaim(text=text) for text in observations),
                        checks=tuple(
                            modules.TaskCheck(name=name, status=check_status)
                            for name, check_status in request.checks
                        ),
                        remaining_work=tuple(request.todo),
                    ),
                )
            )

            # 6. Continuity projection for the Scope.
            continuity = await runtime.work.for_scope(scope_id).continuity()
            return {
                "scope_id": scope_id,
                "contract": _receipt(contract_receipt),
                "handoff": handoff_ref.model_dump(mode="json"),
                "ack": {
                    "status": "accepted",
                    "resolution_status": str(resolution.status),
                    "selection": str(resolution.selection),
                    "selected_revision": (
                        None
                        if resolution.selected_revision is None
                        else resolution.selected_revision.model_dump(mode="json")
                    ),
                    "receipt": _receipt(acknowledgement.receipt),
                },
                "outcome": _receipt(outcome_receipt),
                "continuity": {
                    "total_event_count": continuity.total_event_count,
                    "coverage": continuity.coverage.model_dump(mode="json"),
                    "events": [event.model_dump(mode="json") for event in continuity.events],
                },
                "disposition": disposition,
                "truncated": dict(dropped),
            }


def cast_status(value: str) -> TaskOutcomeStatus:
    """Narrow a validated status string for static type checkers."""

    return value  # type: ignore[return-value]


def _record(record: Any) -> dict[str, Any]:
    """Convert one ``AtomicMemoryRecord`` into the toolkit's ``StateEntry`` shape."""

    return {
        "kind": record.artifact.content.kind,
        "text": record.artifact.content.text,
        "ref": record.ref.model_dump(mode="json"),
        "state": str(record.state.state),
    }


def _receipt(receipt: Any) -> dict[str, Any]:
    """Serialize one ``WorkSourceReceipt`` as a plain, JSON-ready dict."""

    return {
        "kind": str(receipt.kind),
        "source_ref": receipt.source_ref.model_dump(mode="json"),
        "position": receipt.position,
        "content_digest": receipt.content_digest,
    }
