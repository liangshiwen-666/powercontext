"""集中读取环境配置。所有密钥只从 .env / 环境变量来，不写进代码。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    llm_base_url: str = _get("LLM_BASE_URL")
    llm_api_key: str = _get("LLM_API_KEY")
    llm_model: str = _get("LLM_MODEL")

    embedding_mode: str = _get("EMBEDDING_MODE", "auto").lower()
    embedding_base_url: str = _get("EMBEDDING_BASE_URL")
    embedding_api_key: str = _get("EMBEDDING_API_KEY")
    embedding_model: str = _get("EMBEDDING_MODEL")

    seekdb_mode: str = _get("SEEKDB_MODE", "embedded").lower()
    seekdb_path: str = _get("SEEKDB_PATH", "./data/seekdb")
    seekdb_host: str = _get("SEEKDB_HOST", "127.0.0.1")
    seekdb_port: int = int(_get("SEEKDB_PORT", "2881"))
    seekdb_user: str = _get("SEEKDB_USER", "root")
    seekdb_password: str = _get("SEEKDB_PASSWORD")
    seekdb_database: str = _get("SEEKDB_DATABASE", "study_coach")

    powercontext_base_url: str = _get("POWERCONTEXT_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    scope_prefix: str = _get("POWERCONTEXT_SCOPE_PREFIX", "study-coach")

    data_dir: Path = ROOT / "data"
    sample_dir: Path = ROOT / "data" / "samples"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key and self.llm_model)

    @property
    def api_embedding_enabled(self) -> bool:
        return bool(self.embedding_api_key and self.embedding_model)

    @property
    def use_api_embedding(self) -> bool:
        if self.embedding_mode == "api":
            return True
        if self.embedding_mode == "local":
            return False
        return self.api_embedding_enabled


settings = Settings()
