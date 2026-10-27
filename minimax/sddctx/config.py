"""Configuration and path resolution for the SDD context toolkit.

All paths can be overridden with environment variables so the same code runs
from a clean checkout, from a different working directory, or inside tests.

Environment variables
---------------------
SDDCTX_ROOT            override the ``minimax/`` root (default: parent of this package)
SDDCTX_DEMO            override the business project root (default: ``$SDDCTX_ROOT/demo``)
SDDCTX_DATA            override the state/data directory (default: ``$SDDCTX_ROOT/.data``)
SEEKDB_HOST/PORT/USER/PASSWORD/DATABASE
                       seekdb server connection (default: 127.0.0.1:2881, root, sddctx)
SDDCTX_SEEKDB_EMBEDDED_DIR
                       use embedded seekdb (pylibseekdb) with this data directory instead
MINIMAX_API_KEY        MiniMax API key (required only for ``--live`` patch generation)
MINIMAX_BASE_URL       default: https://api.minimax.cn/v1
MINIMAX_MODEL          default: MiniMax-M3.1-Flash
MINIMAX_REASONING_EFFORT  default: high
MINIMAX_MAX_COMPLETION_TOKENS  default: 131072
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
MINIMAX_ROOT = PACKAGE_DIR.parent


def _path_env(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    if raw:
        return Path(raw).expanduser().resolve()
    return default


@dataclass(frozen=True)
class SeekDBConfig:
    """Connection settings for the seekdb server (MySQL protocol)."""

    host: str = "127.0.0.1"
    port: int = 2881
    user: str = "root"
    password: str = ""
    database: str = "sddctx"
    embedded_dir: Path | None = None

    @classmethod
    def from_env(cls) -> "SeekDBConfig":
        embedded = os.environ.get("SDDCTX_SEEKDB_EMBEDDED_DIR")
        return cls(
            host=os.environ.get("SEEKDB_HOST", "127.0.0.1"),
            port=int(os.environ.get("SEEKDB_PORT", "2881")),
            user=os.environ.get("SEEKDB_USER", "root"),
            password=os.environ.get("SEEKDB_PASSWORD", ""),
            database=os.environ.get("SEEKDB_DATABASE", "sddctx"),
            embedded_dir=Path(embedded).expanduser().resolve() if embedded else None,
        )

    def describe(self) -> str:
        if self.embedded_dir is not None:
            return f"seekdb embedded:{self.embedded_dir}"
        return f"seekdb server {self.host}:{self.port}/{self.database}"


@dataclass(frozen=True)
class MiniMaxConfig:
    """MiniMax chat-completion settings used for ``--live`` patch generation."""

    api_key: str = ""
    base_url: str = "https://api.minimax.cn/v1"
    model: str = "MiniMax-M3.1-Flash"
    reasoning_effort: str = "high"
    max_completion_tokens: int = 131072
    timeout_s: int = 900

    @classmethod
    def from_env(cls) -> "MiniMaxConfig":
        return cls(
            api_key=os.environ.get("MINIMAX_API_KEY", ""),
            base_url=os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.cn/v1"),
            model=os.environ.get("MINIMAX_MODEL", "MiniMax-M3.1-Flash"),
            reasoning_effort=os.environ.get("MINIMAX_REASONING_EFFORT", "high"),
            max_completion_tokens=int(os.environ.get("MINIMAX_MAX_COMPLETION_TOKENS", "131072")),
            timeout_s=int(os.environ.get("MINIMAX_TIMEOUT_S", "900")),
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key.strip())

    def describe(self) -> str:
        return f"MiniMax {self.model} @ {self.base_url}"


@dataclass(frozen=True)
class ToolkitConfig:
    """Resolved paths and credentials for one toolkit invocation."""

    root: Path
    demo_root: Path
    data_dir: Path
    records_dir: Path
    evidence_dir: Path
    seekdb: SeekDBConfig
    minimax: MiniMaxConfig

    @classmethod
    def from_env(cls) -> "ToolkitConfig":
        root = _path_env("SDDCTX_ROOT", MINIMAX_ROOT)
        data_dir = _path_env("SDDCTX_DATA", root / ".data")
        return cls(
            root=root,
            demo_root=_path_env("SDDCTX_DEMO", root / "demo"),
            data_dir=data_dir,
            records_dir=root / "records",
            evidence_dir=root / "evidence",
            seekdb=SeekDBConfig.from_env(),
            minimax=MiniMaxConfig.from_env(),
        )

    # -- directories -----------------------------------------------------
    @property
    def pc_db_path(self) -> Path:
        return self.data_dir / "powercontext" / "pc.db"

    @property
    def minimax_record_dir(self) -> Path:
        return self.records_dir / "minimax"

    @property
    def patches_dir(self) -> Path:
        return self.root / "patches"

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.pc_db_path.parent,
            self.minimax_record_dir,
            self.evidence_dir / "logs",
            self.evidence_dir / "context",
        ):
            path.mkdir(parents=True, exist_ok=True)

    # -- relative paths --------------------------------------------------
    def relpath(self, path: Path) -> str:
        """Path relative to the ``minimax/`` root, always POSIX style."""

        try:
            return path.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return path.resolve().as_posix()
