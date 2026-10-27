"""seekdb-backed material store for specs, code and tests.

The store keeps one row per addressable slice of engineering material
(``Chunk``) in a single table and retrieves them with MySQL full-text search.
Two deployment modes are supported, both using the same SQL:

* **server mode** (default): a seekdb server reachable over the MySQL protocol
  (``SeekDBConfig.embedded_dir is None``).  Connections go through ``pymysql``.
* **embedded mode**: ``SeekDBConfig.embedded_dir`` is set, so ``pylibseekdb``
  opens a local seekdb runtime in that directory instead.

Design notes (validated against seekdb 1.4.0, see ``_research/seekdb-usage.md``):

* The full-text index **must** use ``WITH PARSER ik``.  seekdb's default
  ``space`` parser does not segment Chinese, so queries such as ``持久化``
  silently return zero rows.
* ``MATCH(body)`` must reference exactly the indexed column list
  (``ft_body(body)``); a mismatch raises ``ERROR 1191``.
* ``UNIQUE KEY (path, line_start)`` makes ``REPLACE INTO`` an idempotent
  re-ingest, which is what ``ingest()`` relies on.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from .config import SeekDBConfig
from .protocols import Chunk, DocKind, SearchHit

DOC_KINDS: tuple[DocKind, ...] = ("spec", "code", "test")
SNIPPET_CHARS = 160
DEFAULT_LIMIT = 10

_TABLE = "ctx_doc"

_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  kind         VARCHAR(16)  NOT NULL,
  path         VARCHAR(512) NOT NULL,
  line_start   INT NOT NULL,
  line_end     INT NOT NULL,
  title        VARCHAR(255) NOT NULL DEFAULT '',
  body         MEDIUMTEXT NOT NULL,
  symbol       VARCHAR(255) NOT NULL DEFAULT '',
  updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_doc (path, line_start),
  KEY idx_kind (kind),
  FULLTEXT KEY ft_body (body) WITH PARSER ik
) DEFAULT CHARSET = utf8mb4
"""

_SERVER_HINT = (
    "请确认 seekdb 已启动（`systemctl start seekdb`，端口 2881），"
    "或用环境变量 SEEKDB_HOST/SEEKDB_PORT/SEEKDB_USER/SEEKDB_PASSWORD/"
    "SEEKDB_DATABASE 指定连接参数。"
)

_EMBEDDED_HINT = (
    "嵌入式模式需要 pylibseekdb：`pip install pylibseekdb`（仅 Linux/macOS），"
    "或改用 server 模式（不设 SDDCTX_SEEKDB_EMBEDDED_DIR）。"
)


class StoreError(RuntimeError):
    """Raised when the seekdb material store cannot be used.

    The message always names the target instance and a concrete remedy so that
    a failed demo run is actionable instead of a bare driver traceback.
    """


def _like_contains(value: str) -> str:
    """Build a parameterised ``%value%`` pattern for ``path LIKE``.

    ``%``, ``_`` and ``\\`` are escaped so a caller-supplied filter cannot turn
    into a wildcard pattern (values are still bound as SQL parameters).
    """

    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _sql_literal(value: Any) -> str:
    """Render a Python value as a MySQL literal (embedded driver has no params)."""

    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    text = str(value)
    return "'" + text.replace("\\", "\\\\").replace("'", "''") + "'"


def _inline_params(sql: str, params: Sequence[Any]) -> str:
    """Inline ``%s`` placeholders for drivers without parameter binding.

    ``pylibseekdb.Cursor.execute`` only accepts a single SQL string, so the
    embedded route must render literals client-side.  The server route never
    takes this path (``pymysql`` binds parameters natively).
    """

    parts = sql.split("%s")
    expected = len(parts) - 1
    if expected != len(params):
        raise StoreError(
            f"SQL 参数数量不匹配：占位符 {expected} 个，参数 {len(params)} 个；"
            f"SQL: {' '.join(sql.split())[:160]}"
        )
    rendered = [parts[0]]
    for value, tail in zip(params, parts[1:]):
        rendered.append(_sql_literal(value))
        rendered.append(tail)
    return "".join(rendered)


class MaterialStore:
    """Read/write access to the seekdb ``ctx_doc`` material table."""

    def __init__(self, config: SeekDBConfig) -> None:
        if not isinstance(config, SeekDBConfig):
            raise TypeError(f"config must be a SeekDBConfig, got {type(config).__name__}")
        self.config = config
        self._conn: Any | None = None
        self._instance: Any | None = None
        self._schema_ready = False

    # -- construction ----------------------------------------------------
    @classmethod
    def from_config(cls, cfg: SeekDBConfig) -> "MaterialStore":
        """Create a store for ``cfg`` (no connection is opened until needed)."""

        return cls(cfg)

    @property
    def table(self) -> str:
        """Fully qualified table name (works in both server and embedded mode)."""

        return f"`{self.config.database}`.`{_TABLE}`"

    @property
    def embedded(self) -> bool:
        return self.config.embedded_dir is not None

    # -- connection management -------------------------------------------
    def _ensure_conn(self) -> None:
        if self._conn is not None:
            return
        if self.embedded:
            self._open_embedded()
        else:
            self._connect_server()

    def _connect_server(self) -> None:
        try:
            import pymysql
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise StoreError(
                "pymysql 未安装（server 模式需要它）：`pip install pymysql`。"
            ) from exc

        target = f"{self.config.host}:{self.config.port}"
        try:
            conn = pymysql.connect(
                host=self.config.host,
                port=self.config.port,
                user=self.config.user,
                password=self.config.password,
                database=None,
                charset="utf8mb4",
                autocommit=True,
                connect_timeout=5,
            )
        except Exception as exc:
            raise StoreError(
                f"无法连接 seekdb server（目标 {target}，库 {self.config.database}）："
                f"{type(exc).__name__}: {exc}。{_SERVER_HINT}"
            ) from exc

        try:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self.config.database}` "
                    "DEFAULT CHARSET = utf8mb4"
                )
            finally:
                cursor.close()
            conn.select_db(self.config.database)
        except Exception as exc:
            conn.close()
            raise StoreError(
                f"连接 seekdb 成功（{target}），但初始化数据库 "
                f"`{self.config.database}` 失败：{type(exc).__name__}: {exc}。{_SERVER_HINT}"
            ) from exc
        self._conn = conn

    def _open_embedded(self) -> None:
        directory = self.config.embedded_dir
        assert directory is not None  # narrowed by ``embedded``
        try:
            import pylibseekdb
        except ImportError as exc:
            raise StoreError(
                f"无法使用嵌入式 seekdb（data dir: {directory}）：{_EMBEDDED_HINT}"
            ) from exc

        try:
            directory.mkdir(parents=True, exist_ok=True)
            instance = pylibseekdb.open(db_dir=str(directory))
        except Exception as exc:
            raise StoreError(
                f"无法在 {directory} 启动嵌入式 seekdb：{type(exc).__name__}: {exc}。"
                f"{_EMBEDDED_HINT}"
            ) from exc

        try:
            try:
                conn = instance.connect(database="test", autocommit=True)
            except Exception:
                # Some builds do not ship the ``test`` database; retry with the
                # driver default before giving up.
                conn = instance.connect(autocommit=True)
            cursor = conn.cursor()
            try:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self.config.database}` "
                    "DEFAULT CHARSET = utf8mb4"
                )
            finally:
                cursor.close()
        except Exception as exc:
            try:
                instance.close()
            except Exception:  # pragma: no cover - best effort cleanup
                pass
            raise StoreError(
                f"嵌入式 seekdb 已在 {directory} 启动，但初始化数据库 "
                f"`{self.config.database}` 失败：{type(exc).__name__}: {exc}。{_EMBEDDED_HINT}"
            ) from exc

        self._instance = instance
        self._conn = conn

    # -- low level helpers ------------------------------------------------
    def _cursor(self) -> Any:
        conn = self._conn
        if conn is None:  # pragma: no cover - defensive
            raise StoreError("seekdb 连接尚未建立。/ " + _SERVER_HINT)
        return conn.cursor()

    def _execute_on(self, cursor: Any, sql: str, params: Sequence[Any] | None = None) -> None:
        """Run ``sql`` on ``cursor`` honouring the driver's binding support."""

        if self.embedded:
            cursor.execute(_inline_params(sql, list(params or [])))
        else:
            cursor.execute(sql, tuple(params) if params is not None else None)

    def _fetch(self, sql: str, params: Sequence[Any] | None = None) -> list[tuple]:
        self._ensure_conn()
        cursor = self._cursor()
        try:
            self._execute_on(cursor, sql, params)
            rows = cursor.fetchall()
            return list(rows or [])
        except StoreError:
            raise
        except Exception as exc:
            raise StoreError(
                f"seekdb 查询失败（{self.config.describe()}）：{type(exc).__name__}: {exc}；"
                f"SQL: {' '.join(sql.split())[:160]}"
            ) from exc
        finally:
            self._close_cursor(cursor)

    def _execute(self, sql: str, params: Sequence[Any] | None = None) -> None:
        self._ensure_conn()
        cursor = self._cursor()
        try:
            self._execute_on(cursor, sql, params)
        except StoreError:
            raise
        except Exception as exc:
            raise StoreError(
                f"seekdb 写入失败（{self.config.describe()}）：{type(exc).__name__}: {exc}；"
                f"SQL: {' '.join(sql.split())[:160]}"
            ) from exc
        finally:
            self._close_cursor(cursor)

    def _executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> None:
        self._ensure_conn()
        cursor = self._cursor()
        try:
            if self.embedded:
                # ``pylibseekdb.Cursor`` has no ``executemany``/binding; inline
                # each row and execute one statement at a time.
                for row in rows:
                    self._execute_on(cursor, sql, row)
                return
            try:
                cursor.executemany(sql, rows)
            except AttributeError:
                # Very defensive: fall back to one statement at a time.
                for row in rows:
                    self._execute_on(cursor, sql, row)
        except StoreError:
            raise
        except Exception as exc:
            raise StoreError(
                f"seekdb 批量写入失败（{self.config.describe()}）："
                f"{type(exc).__name__}: {exc}；SQL: {' '.join(sql.split())[:160]}"
            ) from exc
        finally:
            self._close_cursor(cursor)

    @staticmethod
    def _close_cursor(cursor: Any) -> None:
        try:
            cursor.close()
        except Exception:  # pragma: no cover - driver dependent
            pass

    # -- schema -----------------------------------------------------------
    def init_schema(self) -> None:
        """Create the database (if needed) and the ``ctx_doc`` table."""

        self._ensure_conn()
        if self._schema_ready:
            return
        self._execute(_DDL.format(table=self.table))
        self._schema_ready = True

    # -- writes -----------------------------------------------------------
    def reset(self, kinds: Sequence[str] | None = None) -> int:
        """Delete all rows (or only rows of the given kinds); return row count."""

        selected = self._normalise_kinds(kinds) if kinds is not None else None
        self.init_schema()
        before = self._count(selected)
        if selected:
            placeholders = ", ".join(["%s"] * len(selected))
            self._execute(
                f"DELETE FROM {self.table} WHERE kind IN ({placeholders})", list(selected)
            )
        else:
            self._execute(f"DELETE FROM {self.table}")
        after = self._count(selected)
        return max(before - after, 0)

    def upsert(self, chunks: Iterable[Chunk]) -> int:
        """Insert or replace chunks; return the number of distinct rows written."""

        prepared: dict[tuple[str, int], tuple[Any, ...]] = {}
        for raw in chunks:
            chunk = self._normalise_chunk(raw)
            prepared[(chunk["path"], chunk["line_start"])] = (
                chunk["kind"],
                chunk["path"],
                chunk["line_start"],
                chunk["line_end"],
                chunk["title"],
                chunk["body"],
                chunk["symbol"],
            )
        if not prepared:
            return 0
        self.init_schema()
        sql = (
            f"REPLACE INTO {self.table}"
            " (kind, path, line_start, line_end, title, body, symbol)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)"
        )
        self._executemany(sql, list(prepared.values()))
        return len(prepared)

    # -- reads ------------------------------------------------------------
    def search(
        self,
        query: str,
        kinds: Sequence[str],
        limit: int = DEFAULT_LIMIT,
        *,
        path_contains: str | None = None,
    ) -> list[SearchHit]:
        """Full-text search across the given kinds (one SQL statement).

        ``citation`` is ``"<path>:<line_start>-<line_end>"`` so the caller can
        point a reader at the exact slice of the workspace.

        ``path_contains`` adds a parameterised ``path LIKE %...%`` filter.  The
        context assembler uses it to make sure both ``tests/target`` and
        ``tests/regression`` suites are represented: the directory name only
        lives in ``path``, never in the chunk body, and full-text tokens such
        as ``test_filter_by_status`` are not split on ``_`` — so a body query
        for ``"test"`` cannot find them.  When ``path_contains`` is given and
        the combined full-text + path query returns nothing, the store falls
        back to enumerating the path-filtered rows (``score = 0.0``, ordered by
        path/line) so a suite can always be selected.  Without ``path_contains``
        the behaviour is exactly the previous full-text query.

        Each hit also carries its raw ``body`` in addition to the 160-char
        ``snippet``; the contract ``SearchHit`` is a ``TypedDict`` (minimum
        keys), and the context pack uses the full body for its excerpts.
        """

        text = (query or "").strip()
        if not text and path_contains is None:
            return []
        if limit <= 0:
            raise ValueError(f"limit must be positive, got {limit}")
        selected = self._normalise_kinds(kinds) if kinds else DOC_KINDS
        self.init_schema()

        rows: list[tuple[Any, ...]] = []
        if text:
            rows = self._search_rows(text, selected, path_contains, limit, use_text=True)
        if not rows and path_contains is not None:
            # Suite enumeration: filter by path only.
            rows = self._search_rows("", selected, path_contains, limit, use_text=False)
        return self._rows_to_hits(rows)

    def list_chunks(
        self,
        kinds: Sequence[str],
        *,
        path_contains: str | None = None,
        limit: int = 200,
    ) -> list[SearchHit]:
        """Deterministically enumerate chunks (no full-text relevance at all).

        ``SELECT ... WHERE kind IN (...) [AND path LIKE %s] ORDER BY path,
        line_start LIMIT %s`` — every row gets ``score = 0.0`` and the same
        citation/snippet/body shape as :meth:`search`.  The context assembler
        uses this to list a whole suite (e.g. all 18 regression tests) without
        relying on keyword luck.
        """

        if limit <= 0:
            raise ValueError(f"limit must be positive, got {limit}")
        selected = self._normalise_kinds(kinds) if kinds else DOC_KINDS
        self.init_schema()

        placeholders = ", ".join(["%s"] * len(selected))
        sql = (
            "SELECT kind, path, line_start, line_end, title, symbol, body,"
            " 0.0 AS score,"
            f" LEFT(REPLACE(body, '\\n', ' '), {SNIPPET_CHARS}) AS snippet"
            f" FROM {self.table}"
            f" WHERE kind IN ({placeholders})"
        )
        params: list[Any] = list(selected)
        if path_contains:
            sql += " AND path LIKE %s"
            params.append(_like_contains(path_contains))
        sql += " ORDER BY path, line_start LIMIT %s"
        params.append(limit)
        return self._rows_to_hits(self._fetch(sql, params))

    def _rows_to_hits(self, rows: Sequence[tuple[Any, ...]]) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for kind, path, line_start, line_end, title, symbol, body, score, snippet in rows:
            hit = SearchHit(
                kind=kind,
                path=str(path),
                line_start=int(line_start),
                line_end=int(line_end),
                title=str(title or ""),
                symbol=str(symbol or ""),
                score=float(score or 0.0),
                snippet=str(snippet or "")[:SNIPPET_CHARS],
                citation=f"{path}:{line_start}-{line_end}",
            )
            # Extra (non-required) key: lets the context assembler quote the
            # chunk without a second round-trip.
            hit["body"] = str(body or "")  # type: ignore[typeddict-unknown-key]
            hits.append(hit)
        return hits

    def _search_rows(
        self,
        text: str,
        selected: Sequence[str],
        path_contains: str | None,
        limit: int,
        *,
        use_text: bool,
    ) -> list[tuple[Any, ...]]:
        if use_text:
            score_sql = "MATCH(body) AGAINST(%s)"
            params: list[Any] = [text]
        else:
            score_sql = "0.0"
            params = []

        sql = (
            "SELECT kind, path, line_start, line_end, title, symbol, body,"
            f" {score_sql} AS score,"
            f" LEFT(REPLACE(body, '\\n', ' '), {SNIPPET_CHARS}) AS snippet"
            f" FROM {self.table}"
        )

        conditions: list[str] = []
        where_params: list[Any] = []
        if use_text:
            conditions.append("MATCH(body) AGAINST(%s)")
            where_params.append(text)
        placeholders = ", ".join(["%s"] * len(selected))
        conditions.append(f"kind IN ({placeholders})")
        where_params.extend(selected)
        if path_contains:
            conditions.append("path LIKE %s")
            where_params.append(_like_contains(path_contains))

        sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY score DESC" if use_text else " ORDER BY path, line_start"
        sql += " LIMIT %s"
        return self._fetch(sql, [*params, *where_params, limit])

    def stats(self) -> dict[str, int]:
        """Return per-kind row counts (all standard kinds are always present)."""

        self.init_schema()
        counts: dict[str, int] = {kind: 0 for kind in DOC_KINDS}
        for kind, total in self._fetch(
            f"SELECT kind, COUNT(*) FROM {self.table} GROUP BY kind"
        ):
            counts[str(kind)] = int(total)
        return counts

    def close(self) -> None:
        """Close the connection and (embedded mode) the local runtime."""

        conn, self._conn = self._conn, None
        instance, self._instance = self._instance, None
        self._schema_ready = False
        if conn is not None:
            try:
                conn.close()
            except Exception:  # pragma: no cover - driver dependent
                pass
        if instance is not None:
            try:
                instance.close()
            except Exception:  # pragma: no cover - driver dependent
                pass

    # -- context manager sugar -------------------------------------------
    def __enter__(self) -> "MaterialStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- validation helpers ----------------------------------------------
    def _count(self, kinds: Sequence[str] | None) -> int:
        if kinds:
            placeholders = ", ".join(["%s"] * len(kinds))
            rows = self._fetch(
                f"SELECT COUNT(*) FROM {self.table} WHERE kind IN ({placeholders})",
                list(kinds),
            )
        else:
            rows = self._fetch(f"SELECT COUNT(*) FROM {self.table}")
        return int(rows[0][0]) if rows else 0

    @staticmethod
    def _normalise_kinds(kinds: Sequence[str]) -> tuple[str, ...]:
        if isinstance(kinds, str):
            raise TypeError("kinds must be a sequence of kind names, not a string")
        selected: list[str] = []
        for kind in kinds:
            if kind not in DOC_KINDS:
                raise ValueError(
                    f"unknown document kind {kind!r}; expected one of {', '.join(DOC_KINDS)}"
                )
            if kind not in selected:
                selected.append(kind)
        if not selected:
            raise ValueError(f"kinds must not be empty; expected one of {', '.join(DOC_KINDS)}")
        return tuple(selected)

    @staticmethod
    def _normalise_chunk(raw: Chunk) -> Chunk:
        if not isinstance(raw, dict):
            raise TypeError(f"chunk must be a dict, got {type(raw).__name__}")
        missing = [
            key
            for key in ("kind", "path", "line_start", "line_end", "title", "symbol", "body")
            if key not in raw
        ]
        if missing:
            raise ValueError(f"chunk is missing required keys: {', '.join(missing)}")
        kind = raw["kind"]
        if kind not in DOC_KINDS:
            raise ValueError(
                f"unknown document kind {kind!r}; expected one of {', '.join(DOC_KINDS)}"
            )
        path = str(raw["path"] or "").strip()
        if not path:
            raise ValueError("chunk path must not be empty")
        body = raw["body"]
        if not isinstance(body, str) or not body.strip():
            raise ValueError(f"chunk body must be a non-empty string (path={path!r})")
        line_start = int(raw["line_start"])
        line_end = int(raw["line_end"])
        if line_start < 1:
            raise ValueError(f"line_start must be 1-based (path={path!r}, got {line_start})")
        if line_end < line_start:
            raise ValueError(
                f"line_end must be >= line_start (path={path!r}, "
                f"{line_start}..{line_end})"
            )
        return Chunk(
            kind=kind,  # type: ignore[arg-type]
            path=path,
            line_start=line_start,
            line_end=line_end,
            title=str(raw["title"] or "")[:255],
            # ``symbol``/``title`` are VARCHAR(255); clamp instead of failing.
            symbol=str(raw["symbol"] or "")[:255],
            body=body,
        )
