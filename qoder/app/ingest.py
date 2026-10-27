"""资料解析与切块：把 md / txt / pdf 变成带出处的片段。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

MAX_CHARS = 800
OVERLAP = 120
SENTENCE_RE = re.compile(r"(?<=[。！？；!?;\n])")


@dataclass
class Chunk:
    id: str
    doc_id: str
    title: str
    section: str
    page: int
    text: str
    tags: list[str] = field(default_factory=list)

    @property
    def citation(self) -> str:
        parts = [self.title]
        if self.section:
            parts.append(self.section)
        if self.page:
            parts.append(f"第 {self.page} 页")
        return " · ".join(parts)


def doc_id_for(path: Path) -> str:
    return hashlib.sha1(path.name.encode("utf-8")).hexdigest()[:12]


def _split_text(text: str) -> list[str]:
    """按句子边界累积到 MAX_CHARS，保留 OVERLAP 重叠。"""
    sentences = [s for s in SENTENCE_RE.split(text) if s.strip()]
    chunks: list[str] = []
    buf = ""
    for sentence in sentences:
        if len(buf) + len(sentence) <= MAX_CHARS:
            buf += sentence
            continue
        if buf.strip():
            chunks.append(buf.strip())
        tail = buf[-OVERLAP:] if len(buf) > OVERLAP else buf
        buf = tail + sentence
    if buf.strip():
        chunks.append(buf.strip())
    return chunks


def _parse_markdown(text: str) -> list[tuple[str, int, str]]:
    """返回 (章节标题, 页码, 正文) 三元组。markdown 没有页码，统一记 0。"""
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in text.splitlines():
        if line.startswith("#"):
            title = line.lstrip("#").strip()
            sections.append((title, []))
        else:
            sections[-1][1].append(line)
    out = []
    for title, lines in sections:
        body = "\n".join(lines).strip()
        if body:
            out.append((title, 0, body))
    return out


def _parse_pdf(path: Path) -> list[tuple[str, int, str]]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    out = []
    for index, page in enumerate(reader.pages, start=1):
        body = (page.extract_text() or "").strip()
        if body:
            out.append(("", index, body))
    return out


def parse_file(path: Path) -> list[Chunk]:
    suffix = path.suffix.lower()
    doc_id = doc_id_for(path)
    title = path.stem

    if suffix == ".pdf":
        segments = _parse_pdf(path)
    else:
        raw = path.read_text(encoding="utf-8", errors="ignore")
        segments = _parse_markdown(raw) if suffix in {".md", ".markdown"} else [("", 0, raw)]

    chunks: list[Chunk] = []
    for section, page, body in segments:
        for index, piece in enumerate(_split_text(body)):
            chunks.append(
                Chunk(
                    id=f"{doc_id}-{page}-{len(chunks)}",
                    doc_id=doc_id,
                    title=title,
                    section=section,
                    page=page,
                    text=piece,
                )
            )
    return chunks
