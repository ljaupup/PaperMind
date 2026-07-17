# 阶段 3：全文解析与 RAG 评测实施手册

> **目标版本：** `v0.4.0`
>
> **阶段目标：** 以论文 PDF 全文替代“只检索标题和摘要”的知识库，并建立可重复、可量化的检索与回答质量评测闭环。
>
> **完成后用户能做什么：** 在专题内得到带论文、章节和页码来源的回答；系统能用指标解释检索策略为何改进，而不是凭主观感觉换模型。

阶段 3 是 PaperMind RAG 从 Demo 走向可信应用的关键阶段。它的优先级高于论文比较、Idea 卡片等生成型功能：没有可靠证据层，后续功能只会放大错误。

> **默认项目位置：** 所有命令和相对路径均以 WSL 中已完成阶段 2 的 `/home/lija/PaperMind/` 为根目录；先执行 `cd /home/lija/PaperMind`，不要在其他位置创建第二份项目。

> **实现裁决规则：** 前半部分的短代码块用于解释职责；真正保存的同名文件以“Document / Chunk Repository 与全文 API 完整实现”和“IndexJob worker 与全文 API 实现包”中的版本为准。遇到同一文件的早期片段，只阅读，不与后文完整版本拼接。

---

## 0. 先认识全文证据与分块

PDF 是文件格式，不是适合直接检索的知识单元。系统先提取每页文本，再把相邻文本切成较小的 **Chunk（文本块）**；每个块保留页码，检索到它时才能让用户回到原文核对。**评测集**是一组人工确认过答案或证据位置的问题，用于比较改动前后的检索效果。

先用纯文本理解分块：

```python
"""演示保留页码的最小文本块。"""


def make_chunk(page: int, text: str) -> dict[str, object]:
    """创建包含页码和文本的证据块。

    :param page: 文本所在的 PDF 页码。
    :param text: 从该页提取的文本。
    :return: 可用于检索和引用的最小证据字典。
    """
    return {"page_start": page, "page_end": page, "text": text}
```

`make_chunk(3, "方法")` 返回的字典同时保存文本与第 3 页位置。后续再将其替换为 `DocumentChunk` 模型、解析器和向量索引。

## 0. 范围与原则

### 本阶段固定基线

| 项目 | 选择 | 原因 |
| --- | --- | --- |
| 初始 PDF 解析器 | PyMuPDF | 英文 arXiv 论文页码提取稳定，依赖较轻，适合作为可控基线。 |
| 文档真相来源 | 本地对象路径/数据卷 + PostgreSQL 元数据 | PDF 文件、解析结果、版本和状态必须可追溯。 |
| 检索基线 | 当前 Chroma 语义检索 | 先量化已有方案，再比较结构分块、混合检索、重排序。 |
| 评测集 | 30–50 条人工标注专题问题 | 规模可维护，足以做回归与策略比较。 |

### 不做什么

- 不一次性接入多个 PDF 解析器、多个 Embedding、多个重排模型。
- 不在没有评测基线时宣称“检索更好”。
- 不要求模型判断论文结论真伪；系统只提供可追溯证据。
- 不把解析失败的论文直接删除。

---

## 1. 先建立可追溯的全文数据链

创建 `app/domain/documents/__init__.py`：

```python
"""全文文档与可追溯分块领域。"""
```

### 1.1 文档生命周期

```text
Paper 元数据
  → Document 下载记录
  → PDF 文件与 SHA-256
  → 解析结果与解析器版本
  → 结构化段落/章节
  → DocumentChunk 与分块策略版本
  → Embedding 与索引版本
  → 检索结果和回答来源
```

新增领域概念：

| 模型 | 关键字段 | 用途 |
| --- | --- | --- |
| `Document` | `id`、`paper_id`、`source_url`、`file_path`、`sha256`、`download_status`、`parse_status`、`parser_version` | 记录 PDF 获得和解析状态。 |
| `DocumentSection` | `id`、`document_id`、`title`、`level`、`page_start`、`page_end`、`order` | 表达章节结构；解析器不可靠时允许为空。 |
| `DocumentChunk` | `id`、`document_id`、`section_id`、`page_start`、`page_end`、`text`、`chunk_index`、`chunker_version` | 向量检索的最小证据单元。 |
| `IndexVersion` | `id`、`embedding_model`、`chunker_version`、`source_snapshot`、`status` | 保证评测和线上检索知道使用了哪套索引。 |

### 1.2 文件与数据库边界

- PDF 文件不直接写入 PostgreSQL `bytea`；保存在受 Docker 数据卷管理的目录或对象存储，数据库只存路径、哈希、来源与状态。
- 下载前记录来源 URL；遵守 arXiv 访问频率与论文页面的使用约束。
- 文件哈希变化时视为新版本，不能悄悄覆盖旧解析结果。
- `metadata_only`、`downloaded`、`parsed`、`parse_failed` 必须是明确状态；前端应展示全文是否可用。

---

## 2. 实施顺序

### 2.1 先写文档领域模型、端口与测试

```text
app/domain/documents/models.py
app/domain/documents/ports.py
tests/unit/test_document_service.py
app/application/document_service.py
```

端口至少包括：

```text
DocumentRepository
DocumentDownloader
DocumentParser
ChunkRepository
IndexRepository
```

应用服务测试应使用 Fake Downloader / Parser，覆盖：下载成功、重复下载、哈希相同、下载失败、解析失败、重试后成功。不要在单元测试中下载真实 PDF。

本节给出全文链路的最小可执行实现。先让 PDF 的页码、块顺序和失败状态正确，再考虑更复杂的章节识别、BM25 或重排序。

#### 安装 PyMuPDF 与创建文档模型

前置条件：当前虚拟环境由 `uv sync` 创建。安装库不代表全文功能已经可用；后续仍需完成 Document 模型、迁移、下载器和解析器。满足后安装：

```bash
uv add pymupdf
```

创建 `app/domain/documents/models.py`：

```python
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ParseStatus(StrEnum):
    """定义全文解析的可用状态。"""

    METADATA_ONLY = "metadata_only"
    DOWNLOADED = "downloaded"
    PARSED = "parsed"
    DOWNLOAD_FAILED = "download_failed"
    PARSE_FAILED = "parse_failed"


class Document(BaseModel):
    """Represent one locally managed full-text document.

    A document records where the PDF came from and whether it is usable.
    """

    id: UUID = Field(default_factory=uuid4)
    paper_id: str
    source_url: str
    file_path: str
    status: ParseStatus = ParseStatus.METADATA_ONLY
    file_sha256: str | None = None


class ParsedBlock(BaseModel):
    """解析器交给领域分块器的统一输入。"""

    text: str
    page_number: int
    block_order: int
    section_title: str | None = None
    section_level: int | None = None


class DocumentChunk(BaseModel):
    """表示可被检索和引用的全文证据块。"""

    id: UUID = Field(default_factory=uuid4)
    document_id: UUID
    paper_id: str
    topic_id: UUID
    chunk_index: int
    text: str
    section_title: str | None = None
    page_start: int
    page_end: int
    chunker_version: str
```

创建 `app/domain/documents/ports.py`：

```python
from pathlib import Path
from typing import Protocol

from app.domain.documents.models import Document, ParsedBlock


class DocumentRepository(Protocol):
    """Define persistence operations needed by the document workflow."""

    def get_or_raise(self, document_id) -> Document: ...
    def save(self, document: Document) -> Document: ...


class PdfDownloader(Protocol):
    """定义下载 PDF 文件的基础设施端口。"""

    def download(self, url: str, destination: Path) -> str:
        """下载文件并返回 SHA-256。

        :param url: PDF 的下载地址。
        :param destination: 保存 PDF 的本地路径。
        :return: 已保存文件的 SHA-256 十六进制摘要。
        """
        ...


class PdfParser(Protocol):
    """定义解析 PDF 为文本块的基础设施端口。"""

    def parse(self, file_path: Path) -> list[ParsedBlock]: ...
```

### 2.2 实现下载与解析适配器

创建：

```text
app/infrastructure/documents/downloader.py
app/infrastructure/documents/pymupdf_parser.py
```

解析器输出应是统一的结构化块，而不是只返回拼接后的整篇字符串：

```text
ParsedBlock(
  text,
  page_number,
  section_title,
  section_level,
  block_order
)
```

`infrastructure/documents/` 负责 PDF 格式、网络和 PyMuPDF；`domain/knowledge/chunking.py` 只接收规范化块并执行分块策略。不要让 `chunking.py` 直接读取 PDF。

#### PDF 下载器与 PyMuPDF 解析器

创建 `app/infrastructure/documents/downloader.py`：

```python
import hashlib
from pathlib import Path

import httpx


class HttpPdfDownloader:
    """通过 HTTP 下载 PDF 并返回文件哈希。"""

    def download(self, url: str, destination: Path) -> str:
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with httpx.stream("GET", url, timeout=60.0, follow_redirects=True) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "pdf" not in content_type.lower():
                raise ValueError(f"expected PDF, got content-type={content_type!r}")
            with destination.open("wb") as output:
                for chunk in response.iter_bytes():
                    output.write(chunk)
                    digest.update(chunk)
        return digest.hexdigest()
```

创建 `app/infrastructure/documents/pymupdf_parser.py`：

```python
from pathlib import Path

import fitz

from app.domain.documents.models import ParsedBlock


class PyMuPdfParser:
    """将 PDF 页面转换为项目内部文本块。"""

    VERSION = "pymupdf-text-v1"

    def parse(self, file_path: Path) -> list[ParsedBlock]:
        document = fitz.open(file_path)
        blocks: list[ParsedBlock] = []
        order = 0
        try:
            for page_index, page in enumerate(document):
                for raw in page.get_text("blocks"):
                    text = " ".join(raw[4].split())
                    if len(text) < 40:
                        continue
                    blocks.append(
                        ParsedBlock(
                            text=text,
                            page_number=page_index + 1,
                            block_order=order,
                        )
                    )
                    order += 1
        finally:
            document.close()
        if not blocks:
            raise ValueError("no usable text blocks extracted from PDF")
        return blocks
```

`httpx.stream()` 返回流式响应上下文；`response.iter_bytes()` 每次产生一段二进制内容，避免整份 PDF 同时放进内存。`fitz.open()` 返回 PyMuPDF 的文档对象，可迭代得到页面；`page.get_text("blocks")` 返回带坐标和文本的块元组，示例只取其中的文本与页码并转换为 `ParsedBlock`。

此版本先不尝试“聪明地”识别章节标题。先保证页码正确，后续再比较字体大小、编号模式或专门解析器带来的收益。

#### 文档应用服务与状态测试

创建 `app/application/document_service.py`。下载失败与解析失败必须写成不同状态，后续页面才能给出正确的恢复提示：

```python
from pathlib import Path
from uuid import UUID

from app.domain.documents.models import Document, ParseStatus, ParsedBlock


class DocumentService:
    """Coordinate PDF download, parsing, and document status updates."""

    def __init__(self, documents, downloader, parser, topics=None) -> None:
        self.documents = documents
        self.downloader = downloader
        self.parser = parser
        self.topics = topics

    def register(self, topic_id: UUID, paper_id: str, source_url: str) -> Document:
        """Create metadata for one topic-visible paper before background indexing."""
        if self.topics is None or self.topics.get_paper_link(topic_id, paper_id) is None:
            raise LookupError("topic paper not found")
        document = Document(
            paper_id=paper_id,
            source_url=source_url,
            file_path=str(Path("data/pdfs") / f"{paper_id.replace('/', '_')}.pdf"),
        )
        return self.documents.save(document)

    def download_and_parse(self, document_id: UUID) -> list[ParsedBlock]:
        """Download one PDF and return its normalized parsed blocks.

        :return: Parsed blocks for a successfully processed document.
        """
        document = self.documents.get_or_raise(document_id)
        try:
            document.file_sha256 = self.downloader.download(document.source_url, Path(document.file_path))
        except Exception:
            document.status = ParseStatus.DOWNLOAD_FAILED
            self.documents.save(document)
            raise

        document.status = ParseStatus.DOWNLOADED
        self.documents.save(document)
        try:
            blocks = self.parser.parse(Path(document.file_path))
        except Exception:
            document.status = ParseStatus.PARSE_FAILED
            self.documents.save(document)
            raise

        document.status = ParseStatus.PARSED
        self.documents.save(document)
        return blocks
```

创建 `tests/unit/test_document_service.py`，不下载真实 PDF：

```python
from pathlib import Path
from uuid import uuid4

import pytest

from app.application.document_service import DocumentService
from app.domain.documents.models import Document, ParseStatus, ParsedBlock


class FakeDocuments:
    def __init__(self, document: Document) -> None:
        self.document = document

    def get_or_raise(self, document_id):
        assert document_id == self.document.id
        return self.document

    def save(self, document: Document) -> Document:
        self.document = document
        return document


class FakeDownloader:
    def download(self, url: str, destination: Path) -> str:
        return "sha256-example"


class FailingDownloader:
    def download(self, url: str, destination: Path) -> str:
        raise OSError("network unavailable")


class FakeParser:
    def parse(self, file_path: Path) -> list[ParsedBlock]:
        return [ParsedBlock(text="body", page_number=1, block_order=0)]


def make_document() -> Document:
    return Document(
        id=uuid4(),
        paper_id="2401.00001",
        source_url="https://example.invalid/paper.pdf",
        file_path="data/papers/2401.00001.pdf",
    )


def test_download_and_parse_marks_document_parsed() -> None:
    document = make_document()
    service = DocumentService(FakeDocuments(document), FakeDownloader(), FakeParser())

    blocks = service.download_and_parse(document.id)

    assert document.status is ParseStatus.PARSED
    assert document.file_sha256 == "sha256-example"
    assert blocks[0].page_number == 1


def test_download_failure_does_not_become_parse_failure() -> None:
    document = make_document()
    service = DocumentService(FakeDocuments(document), FailingDownloader(), FakeParser())

    with pytest.raises(OSError, match="network unavailable"):
        service.download_and_parse(document.id)

    assert document.status is ParseStatus.DOWNLOAD_FAILED
```

```bash
uv run pytest tests/unit/test_document_service.py -q
```

#### 文档与 Chunk 的 PostgreSQL 持久化

创建 `app/infrastructure/persistence/document_models.py`。Document 和 Chunk 必须分别入表，不能只存在于 Chroma 元数据中；PostgreSQL 是状态、版本和来源关系的事实来源：

```python
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.models import Base


class DocumentRecord(Base):
    """Map downloaded full-text metadata to PostgreSQL."""

    __tablename__ = "documents"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.paper_id", ondelete="CASCADE"), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)


class DocumentChunkRecord(Base):
    """Map a versioned, page-addressable document chunk."""

    __tablename__ = "document_chunks"
    __table_args__ = (UniqueConstraint("document_id", "chunk_index", "chunker_version", name="uq_document_chunk_version"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.paper_id", ondelete="CASCADE"), nullable=False, index=True)
    topic_id: Mapped[UUID] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    section_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    chunker_version: Mapped[str] = mapped_column(String(120), nullable=False)
```

阶段 2 已预留 `index_jobs.document_id` 的可空 UUID。本阶段生成 Document 迁移时，必须在 `documents` 建表之后补上该字段的外键：`ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE")`。旧的摘要 IndexJob 保持 `NULL`；全文 worker 仅领取 `document_id IS NOT NULL` 的 Job。

创建 `migrations/versions/0003_documents_and_evaluations.py`。不要把 `index_jobs.document_id` 的外键留在注释或应用代码中；以下迁移让空库与已运行阶段 2 的数据库得到同一结构：

```python
"""add documents and versioned chunks

Revision ID: 0003_documents_and_evaluations
Revises: 0002_jobs_and_subscriptions
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003_documents_and_evaluations"
down_revision = "0002_jobs_and_subscriptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "index_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("topic_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("topics.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("topic_id", "version", name="uq_index_versions_topic_version"),
    )
    op.create_index("ix_index_versions_topic_id", "index_versions", ["topic_id"])
    op.create_index("ix_index_versions_status", "index_versions", ["status"])
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("paper_id", sa.String(), sa.ForeignKey("papers.paper_id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
    )
    op.create_index("ix_documents_status", "documents", ["status"])

    op.create_table(
        "document_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("paper_id", sa.String(), sa.ForeignKey("papers.paper_id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("topics.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("section_title", sa.Text(), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("chunker_version", sa.String(length=120), nullable=False),
        sa.UniqueConstraint("document_id", "chunk_index", "chunker_version", name="uq_document_chunk_version"),
    )
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])
    op.create_index("ix_document_chunks_paper_id", "document_chunks", ["paper_id"])
    op.create_index("ix_document_chunks_topic_id", "document_chunks", ["topic_id"])
    op.create_foreign_key(
        "fk_index_jobs_document_id_documents",
        "index_jobs",
        "documents",
        ["document_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_index_jobs_document_id_documents", "index_jobs", type_="foreignkey")
    op.drop_index("ix_document_chunks_topic_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_paper_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_document_id", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index("ix_documents_status", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_index_versions_status", table_name="index_versions")
    op.drop_index("ix_index_versions_topic_id", table_name="index_versions")
    op.drop_table("index_versions")
```

阶段 3 的迁移已经把 `index_jobs.document_id` 关联到 `documents.id`，所以还必须用下面内容**完整替换** `app/infrastructure/persistence/job_models.py`。不能继续保留阶段 2 中的裸 UUID 声明，否则 ORM 元数据会与数据库约束漂移：

```python
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.models import Base


class CollectionJobRecord(Base):
    __tablename__ = "collection_jobs"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_collection_jobs_idempotency_key"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    topic_id: Mapped[UUID] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), nullable=False, index=True)
    subscription_id: Mapped[UUID | None] = mapped_column(ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    fetched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SubscriptionRecord(Base):
    __tablename__ = "subscriptions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    topic_id: Mapped[UUID] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), nullable=False, index=True)
    keywords: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    categories: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    max_results: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_submission_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IndexJobRecord(Base):
    __tablename__ = "index_jobs"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_index_jobs_idempotency_key"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    topic_id: Mapped[UUID] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), nullable=False, index=True)
    document_id: Mapped[UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True)
    target_index_version: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
```

全文索引还需要保存“哪个版本可供检索”，否则 `IndexExecutionService` 的 `begin/mark_ready/mark_failed` 没有真实持久化实现。创建 `app/infrastructure/persistence/index_version_models.py` 与 `app/infrastructure/persistence/index_version_repository.py`：

```python
# app/infrastructure/persistence/index_version_models.py
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.models import Base


class IndexVersionRecord(Base):
    __tablename__ = "index_versions"
    __table_args__ = (UniqueConstraint("topic_id", "version", name="uq_index_versions_topic_version"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    topic_id: Mapped[UUID] = mapped_column(ForeignKey("topics.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
```

```python
# app/infrastructure/persistence/index_version_repository.py
from uuid import UUID

from sqlalchemy import select, update

from app.infrastructure.persistence.index_version_models import IndexVersionRecord


class PostgresIndexVersionRepository:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    def begin(self, topic_id: UUID, version: str) -> IndexVersionRecord:
        with self.session_factory() as session:
            item = session.scalar(
                select(IndexVersionRecord).where(
                    IndexVersionRecord.topic_id == topic_id,
                    IndexVersionRecord.version == version,
                )
            )
            if item is None:
                item = IndexVersionRecord(topic_id=topic_id, version=version, status="building")
                session.add(item)
            else:
                item.status = "building"
            session.commit()
            session.refresh(item)
            return item

    def mark_ready(self, topic_id: UUID, version: str) -> None:
        with self.session_factory() as session:
            session.execute(
                update(IndexVersionRecord)
                .where(IndexVersionRecord.topic_id == topic_id, IndexVersionRecord.status == "ready")
                .values(status="superseded")
            )
            session.execute(
                update(IndexVersionRecord)
                .where(IndexVersionRecord.topic_id == topic_id, IndexVersionRecord.version == version)
                .values(status="ready")
            )
            session.commit()

    def mark_failed(self, topic_id: UUID, version: str) -> None:
        with self.session_factory() as session:
            session.execute(
                update(IndexVersionRecord)
                .where(IndexVersionRecord.topic_id == topic_id, IndexVersionRecord.version == version)
                .values(status="failed")
            )
            session.commit()

    def get_ready(self, topic_id: UUID):
        with self.session_factory() as session:
            return session.scalar(
                select(IndexVersionRecord)
                .where(IndexVersionRecord.topic_id == topic_id, IndexVersionRecord.status == "ready")
                .order_by(IndexVersionRecord.created_at.desc())
            )
```

将 `0003_documents_and_evaluations.py` 的 `upgrade()` 中、`documents` 建表前加入这个表与索引；`downgrade()` 中在删除 `documents` 前删除它：

```python
op.create_table(
    "index_versions",
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("topic_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("topics.id", ondelete="CASCADE"), nullable=False),
    sa.Column("version", sa.String(length=120), nullable=False),
    sa.Column("status", sa.String(length=16), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.UniqueConstraint("topic_id", "version", name="uq_index_versions_topic_version"),
)
op.create_index("ix_index_versions_topic_id", "index_versions", ["topic_id"])
op.create_index("ix_index_versions_status", "index_versions", ["status"])
```

```python
op.drop_index("ix_index_versions_status", table_name="index_versions")
op.drop_index("ix_index_versions_topic_id", table_name="index_versions")
op.drop_table("index_versions")
```

最后将 `IndexExecutionService` 的三处调用替换为 `self.versions.begin(topic_id, target_version)`、`self.versions.mark_ready(topic_id, target_version)`、`self.versions.mark_failed(topic_id, target_version)`；版本状态始终属于一个专题，不能按裸字符串跨专题更新。

生成迁移后创建 `tests/integration/test_document_repository.py`。它保存一个 Document 和两个 Chunk，重新打开 Session 后断言页码、`chunker_version` 与文本都能读回；再对相同 `(document_id, chunk_index, chunker_version)` 插入第二次，断言唯一约束生效。它会建表并删表，因此只能连接专用空测试库：

```python
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.domain.documents.models import Document, DocumentChunk, ParseStatus
from app.infrastructure.persistence.models import Base, PaperRecord
from app.infrastructure.persistence import document_models as _document_models
from app.infrastructure.persistence import job_models as _job_models
from app.infrastructure.persistence import topic_models as _topic_models
from app.infrastructure.persistence.document_models import DocumentChunkRecord
from app.infrastructure.persistence.document_repository import PostgresChunkRepository, PostgresDocumentRepository
from app.infrastructure.persistence.topic_models import TopicPaperRecord, TopicRecord, WorkspaceRecord


@pytest.mark.integration
def test_document_chunks_persist_and_keep_versioned_unique_constraint() -> None:
    url = os.getenv("PAPER_MIND_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("PAPER_MIND_TEST_POSTGRES_URL is required for integration tests")
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    workspace_id, topic_id = uuid4(), uuid4()
    document = Document(paper_id="paper-a", source_url="https://example.test/paper-a.pdf", file_path="data/pdfs/paper-a.pdf", status=ParseStatus.PARSED)
    chunks = [
        DocumentChunk(document_id=document.id, paper_id="paper-a", topic_id=topic_id, chunk_index=0, text="page one", page_start=1, page_end=1, chunker_version="v1"),
        DocumentChunk(document_id=document.id, paper_id="paper-a", topic_id=topic_id, chunk_index=1, text="page two", page_start=2, page_end=2, chunker_version="v1"),
    ]
    try:
        with sessions() as session:
            session.add_all([
                WorkspaceRecord(id=workspace_id, name=f"test-{workspace_id}"),
                TopicRecord(id=topic_id, workspace_id=workspace_id, name="topic", description="", keywords=["rag"], categories=[]),
                PaperRecord(paper_id="paper-a", title="Paper", abstract="", authors=[], url="https://example.test/paper-a"),
                TopicPaperRecord(topic_id=topic_id, paper_id="paper-a"),
            ])
            session.commit()

        documents = PostgresDocumentRepository(sessions)
        chunk_repository = PostgresChunkRepository(sessions)
        documents.save(document)
        chunk_repository.replace_for_document(document.id, chunks)
        saved = chunk_repository.list_for_topic(topic_id, limit=10)
        assert [(item.page_start, item.chunker_version, item.text) for item in saved] == [
            (1, "v1", "page one"), (2, "v1", "page two"),
        ]

        with sessions() as session:
            session.add(DocumentChunkRecord(**chunks[0].model_dump()))
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
```

```bash
PAPER_MIND_TEST_POSTGRES_URL=<可丢弃的数据库 URL> uv run pytest tests/integration/test_document_repository.py -m integration -q
```

#### Document / Chunk Repository 与全文 API 完整实现

创建 `app/infrastructure/persistence/document_repository.py`。所有读取都以 `topic_id` 过滤；不能只按 `document_id` 查到记录后就返回，避免专题 A 读取专题 B 的全文状态：

```python
from uuid import UUID

from sqlalchemy import delete, select

from app.domain.documents.models import Document, DocumentChunk, ParseStatus
from app.infrastructure.persistence.document_models import DocumentChunkRecord, DocumentRecord
from app.infrastructure.persistence.topic_models import TopicPaperRecord


def document_from_record(record: DocumentRecord, topic_id: UUID) -> Document:
    return Document(
        id=record.id, paper_id=record.paper_id, source_url=record.source_url,
        file_path=record.file_path, status=ParseStatus(record.status), file_sha256=record.file_sha256,
    )


class PostgresDocumentRepository:
    def __init__(self, session_factory) -> None: self.session_factory = session_factory

    def get_for_topic(self, topic_id: UUID, document_id: UUID) -> Document | None:
        with self.session_factory() as session:
            record = session.scalar(
                select(DocumentRecord).join(TopicPaperRecord, TopicPaperRecord.paper_id == DocumentRecord.paper_id).where(
                    DocumentRecord.id == document_id, TopicPaperRecord.topic_id == topic_id
                )
            )
            return None if record is None else document_from_record(record, topic_id)

    def get_or_raise(self, document_id: UUID) -> Document:
        with self.session_factory() as session:
            record = session.get(DocumentRecord, document_id)
            if record is None: raise LookupError("document not found")
            return Document(id=record.id, paper_id=record.paper_id, source_url=record.source_url, file_path=record.file_path,
                            status=ParseStatus(record.status), file_sha256=record.file_sha256)

    def save(self, document: Document) -> Document:
        with self.session_factory() as session:
            record = session.get(DocumentRecord, document.id)
            if record is None:
                record = DocumentRecord(id=document.id, paper_id=document.paper_id, source_url=document.source_url, file_path=document.file_path, status=document.status.value)
                session.add(record)
            else:
                record.status, record.file_sha256 = document.status.value, document.file_sha256
            session.commit(); return document


class PostgresChunkRepository:
    def __init__(self, session_factory) -> None: self.session_factory = session_factory

    def replace_for_document(self, document_id: UUID, chunks: list[DocumentChunk]) -> None:
        with self.session_factory() as session:
            session.execute(delete(DocumentChunkRecord).where(DocumentChunkRecord.document_id == document_id))
            session.add_all([DocumentChunkRecord(**chunk.model_dump()) for chunk in chunks])
            session.commit()

    def list_for_topic(self, topic_id: UUID, *, limit: int) -> list[DocumentChunk]:
        with self.session_factory() as session:
            records = session.scalars(select(DocumentChunkRecord).where(DocumentChunkRecord.topic_id == topic_id).order_by(DocumentChunkRecord.document_id, DocumentChunkRecord.chunk_index).limit(limit)).all()
            return [DocumentChunk.model_validate(record, from_attributes=True) for record in records]

    def get(self, chunk_id: UUID) -> DocumentChunk | None:
        with self.session_factory() as session:
            record = session.get(DocumentChunkRecord, chunk_id)
            return None if record is None else DocumentChunk.model_validate(record, from_attributes=True)

    def list_for_papers(
        self,
        topic_id: UUID,
        paper_ids: list[str],
    ) -> list[DocumentChunk]:
        with self.session_factory() as session:
            records = session.scalars(
                select(DocumentChunkRecord)
                .where(
                    DocumentChunkRecord.topic_id == topic_id,
                    DocumentChunkRecord.paper_id.in_(paper_ids),
                )
                .order_by(DocumentChunkRecord.paper_id, DocumentChunkRecord.chunk_index)
            ).all()
            return [DocumentChunk.model_validate(record, from_attributes=True) for record in records]
```

创建 `app/schemas/documents.py` 与 `app/schemas/search.py`：

```python
from uuid import UUID

from pydantic import BaseModel, Field


class CreateDocumentRequest(BaseModel):
    paper_id: str = Field(min_length=1, max_length=128)
    source_url: str = Field(min_length=1, max_length=2000)


class DocumentResponse(BaseModel):
    id: UUID; paper_id: str; source_url: str; status: str; file_sha256: str | None

    @classmethod
    def from_domain(cls, item): return cls(**item.model_dump())
```

```python
from uuid import UUID

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    index_version: str | None = None


class SourceResponse(BaseModel):
    paper_id: str; chunk_id: UUID; page_start: int; page_end: int; text: str


class SearchResponse(BaseModel):
    sources: list[SourceResponse]; index_version: str


class AskResponse(SearchResponse):
    answer: str
    insufficient_evidence: bool
```

阶段 1 的 `ChromaVectorStore`、`RetrievalService` 和 `RAGService` 只支持摘要 `PaperChunk`，不能与全文 API 混用。以下是阶段 3 的**完整替换版本**；阶段 5 的比较、笔记来源校验与 IndexExecutionService 都使用这一组文件：

阶段 1 的 `app/api/v1/knowledge.py` 因而成为历史接口：保留它只作学习对照，但从阶段 3 起**不得**在最终 `app/api/v1/router.py` 注册它；`search.py` 是 `/topics/{topic_id}/search` 与 `/ask` 的唯一实现。

```python
# app/infrastructure/vector/chroma.py
from typing import Any
from uuid import UUID

import chromadb

from app.domain.documents.models import DocumentChunk
from app.domain.models import PaperChunk


class ChromaVectorStore:
    def __init__(self, host: str, port: int, collection_name: str) -> None:
        self.collection = chromadb.HttpClient(host=host, port=port).get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert_chunks(self, chunks: list[PaperChunk], embeddings: list[list[float]]) -> None:
        """Keep stage-1 abstract chunks available during migration to full text."""
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have equal length")
        if not chunks:
            return
        self.collection.upsert(
            ids=[chunk.chunk_id for chunk in chunks],
            embeddings=embeddings,
            documents=[chunk.text for chunk in chunks],
            metadatas=[{
                "kind": "abstract", "paper_id": chunk.paper_id, "title": chunk.title,
                "url": chunk.url, "pdf_url": chunk.pdf_url or "", "page": chunk.page or 0,
            } for chunk in chunks],
        )

    def upsert_document_chunks(
        self,
        chunks: list[DocumentChunk],
        embeddings: list[list[float]],
        *,
        index_version: str,
    ) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have equal length")
        if not chunks:
            return
        self.collection.upsert(
            ids=[str(chunk.id) for chunk in chunks],
            embeddings=embeddings,
            documents=[chunk.text for chunk in chunks],
            metadatas=[{
                "kind": "document", "topic_id": str(chunk.topic_id), "paper_id": chunk.paper_id,
                "document_id": str(chunk.document_id), "chunk_id": str(chunk.id),
                "page_start": chunk.page_start, "page_end": chunk.page_end,
                "section_title": chunk.section_title or "", "index_version": index_version,
            } for chunk in chunks],
        )

    def query_document_chunks(
        self,
        embedding: list[float],
        *,
        topic_id: UUID,
        index_version: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        result = self.collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            where={"$and": [
                {"kind": {"$eq": "document"}},
                {"topic_id": {"$eq": str(topic_id)}},
                {"index_version": {"$eq": index_version}},
            ]},
        )
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        return [
            {"text": text, **metadata, "score": float(distance)}
            for text, metadata, distance in zip(documents, metadatas, distances, strict=True)
        ]
```

```python
# app/application/retrieval_service.py
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class RetrievedSource:
    paper_id: str
    chunk_id: UUID
    page_start: int
    page_end: int
    text: str
    score: float


@dataclass(frozen=True)
class SearchResult:
    sources: list[RetrievedSource]
    index_version: str


class RetrievalService:
    def __init__(self, embeddings, vector_store, versions) -> None:
        self.embeddings = embeddings
        self.vector_store = vector_store
        self.versions = versions

    async def search(
        self,
        *,
        topic_id: UUID,
        question: str,
        top_k: int,
        index_version: str | None = None,
    ) -> SearchResult:
        version = index_version
        if version is None:
            ready = self.versions.get_ready(topic_id)
            if ready is None:
                return SearchResult(sources=[], index_version="")
            version = ready.version
        embedding = (await self.embeddings.embed_texts([question]))[0]
        rows = self.vector_store.query_document_chunks(
            embedding,
            topic_id=topic_id,
            index_version=version,
            top_k=top_k,
        )
        return SearchResult(
            sources=[
                RetrievedSource(
                    paper_id=row["paper_id"],
                    chunk_id=UUID(row["chunk_id"]),
                    page_start=int(row["page_start"]),
                    page_end=int(row["page_end"]),
                    text=row["text"],
                    score=float(row["score"]),
                )
                for row in rows
            ],
            index_version=version,
        )
```

```python
# app/application/rag_service.py
from dataclasses import dataclass
from uuid import UUID

from app.application.retrieval_service import RetrievedSource


@dataclass(frozen=True)
class AskResult:
    answer: str
    sources: list[RetrievedSource]
    insufficient_evidence: bool
    index_version: str


class RAGService:
    def __init__(self, retrieval, llm) -> None:
        self.retrieval = retrieval
        self.llm = llm

    async def ask(
        self,
        *,
        topic_id: UUID,
        question: str,
        top_k: int,
        index_version: str | None = None,
    ) -> AskResult:
        result = await self.retrieval.search(
            topic_id=topic_id,
            question=question,
            top_k=top_k,
            index_version=index_version,
        )
        if not result.sources:
            return AskResult(
                answer="证据不足，无法回答。",
                sources=[],
                insufficient_evidence=True,
                index_version=result.index_version,
            )
        answer = await self.llm.generate(question, [source.text for source in result.sources])
        return AskResult(
            answer=answer,
            sources=result.sources,
            insufficient_evidence=False,
            index_version=result.index_version,
        )
```

创建 `app/api/v1/documents.py` 与 `app/api/v1/search.py`。Router 只验证范围并调用 Service：

```python
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from app.api.dependencies import get_document_repository, get_document_service
from app.schemas.documents import CreateDocumentRequest, DocumentResponse

router = APIRouter(tags=["documents"])


@router.post("/topics/{topic_id}/documents", response_model=DocumentResponse, status_code=201)
def create_document(topic_id: UUID, request: CreateDocumentRequest, service=Depends(get_document_service)):
    try:
        return DocumentResponse.from_domain(service.register(topic_id, **request.model_dump()))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.get("/topics/{topic_id}/documents/{document_id}", response_model=DocumentResponse)
def get_document(topic_id: UUID, document_id: UUID, repository=Depends(get_document_repository)):
    document = repository.get_for_topic(topic_id, document_id)
    if document is None: raise HTTPException(status_code=404, detail="document not found in topic")
    return DocumentResponse.from_domain(document)
```

```python
from uuid import UUID
from fastapi import APIRouter, Depends
from app.api.dependencies import get_retrieval_service, get_rag_service
from app.schemas.search import AskResponse, SearchRequest, SearchResponse, SourceResponse

router = APIRouter(tags=["search"])

def source(item):
    return SourceResponse(
        paper_id=item.paper_id,
        chunk_id=item.chunk_id,
        page_start=item.page_start,
        page_end=item.page_end,
        text=item.text,
    )

@router.post("/topics/{topic_id}/search", response_model=SearchResponse)
async def search(topic_id: UUID, request: SearchRequest, service=Depends(get_retrieval_service)):
    result = await service.search(topic_id=topic_id, question=request.question, top_k=request.top_k, index_version=request.index_version)
    return SearchResponse(sources=[source(x) for x in result.sources], index_version=result.index_version)

@router.post("/topics/{topic_id}/ask", response_model=AskResponse)
async def ask(topic_id: UUID, request: SearchRequest, service=Depends(get_rag_service)):
    result = await service.ask(topic_id=topic_id, question=request.question, top_k=request.top_k, index_version=request.index_version)
    return AskResponse(answer=result.answer, sources=[source(x) for x in result.sources], insufficient_evidence=result.insufficient_evidence, index_version=result.index_version)
```

创建 API 测试，分别证明跨专题文档隔离、来源页码和拒答语义：

```python
# tests/api/conftest.py
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from app.domain.documents.models import Document, ParseStatus


class FakeSource(BaseModel):
    paper_id: str = "2401.00001"
    chunk_id: UUID = uuid4()
    page_start: int = 3
    page_end: int = 4
    text: str = "method evidence"


class FakeDocumentRepository:
    def __init__(self, topic_id: UUID, document_id: UUID) -> None:
        self.topic_id, self.document_id = topic_id, document_id

    def get_for_topic(self, topic_id: UUID, document_id: UUID):
        if topic_id != self.topic_id or document_id != self.document_id:
            return None
        return Document(id=document_id, paper_id="2401.00001", source_url="https://example.test/p.pdf", file_path="data/p.pdf", status=ParseStatus.PARSED)


class FakeDocumentService:
    def __init__(self, topic_id: UUID) -> None:
        self.topic_id = topic_id

    def register(self, topic_id: UUID, paper_id: str, source_url: str):
        if topic_id != self.topic_id:
            raise LookupError("topic paper not found")
        return Document(paper_id=paper_id, source_url=source_url, file_path="data/pdfs/example.pdf")


class FakeRetrievalService:
    def __init__(self, topic_id: UUID) -> None: self.topic_id = topic_id
    async def search(self, *, topic_id, question, top_k, index_version):
        assert topic_id == self.topic_id
        return SimpleNamespace(sources=[FakeSource()], index_version=index_version or "v1")


class FakeRagService(FakeRetrievalService):
    async def ask(self, *, topic_id, question, top_k, index_version):
        assert topic_id == self.topic_id
        return SimpleNamespace(answer="证据不足", sources=[], insufficient_evidence=True, index_version=index_version or "v1")


class FakeContainer:
    def __init__(self) -> None:
        self.topic_id, self.document_id = uuid4(), uuid4()
        self.document_repository = FakeDocumentRepository(self.topic_id, self.document_id)
        self.document_service = FakeDocumentService(self.topic_id)
        self.retrieval_service = FakeRetrievalService(self.topic_id)
        self.rag_service = FakeRagService(self.topic_id)


@pytest.fixture
def fake_container() -> FakeContainer:
    return FakeContainer()
```

```python
# tests/api/test_document_status.py
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import create_app


def test_document_from_another_topic_is_404(fake_container):
    client = TestClient(create_app(fake_container))
    assert client.get(f"/api/v1/topics/{uuid4()}/documents/{fake_container.document_id}").status_code == 404


def test_register_document_for_topic(fake_container):
    client = TestClient(create_app(fake_container))
    response = client.post(
        f"/api/v1/topics/{fake_container.topic_id}/documents",
        json={"paper_id": "2401.00001", "source_url": "https://example.test/paper.pdf"},
    )
    assert response.status_code == 201
    assert response.json()["paper_id"] == "2401.00001"
```

```python
# tests/api/test_search.py
from fastapi.testclient import TestClient

from app.main import create_app


def test_search_keeps_page_sources_and_refusal_is_separate(fake_container):
    client = TestClient(create_app(fake_container))
    search = client.post(f"/api/v1/topics/{fake_container.topic_id}/search", json={"question": "method"})
    assert search.status_code == 200 and search.json()["sources"][0]["page_start"] == 3
    ask = client.post(f"/api/v1/topics/{fake_container.topic_id}/ask", json={"question": "unknown"})
    assert ask.status_code == 200 and ask.json()["insufficient_evidence"] is True
    assert ask.json()["sources"] == []
```

### 2.3 演进分块逻辑

当前固定字符窗口只适合摘要。全文分块按以下阶段改造：

1. 保留标题和摘要块作为降级证据。
2. 以章节、段落和页码为输入，优先在段落或句子边界切分。
3. 每个 Chunk 继承论文标题、章节标题、页码区间、块顺序和策略版本。
4. 设定最大 token/字符长度与最小长度；过短段落可与相邻段合并。
5. 仅在确有上下文断裂时使用 overlap，不能让大量重复文本占满上下文窗口。

建议在 `domain/knowledge/chunking.py` 中显式维护 `CHUNKER_VERSION`，每改变策略都提升版本，并在索引和评测记录中保存它。

#### 将固定字符切分升级为结构化分块

替换 `app/domain/knowledge/chunking.py` 中新增的全文函数；保留旧 `build_chunks(papers)` 作为摘要降级路径：

```python
from app.domain.documents.models import DocumentChunk, ParsedBlock

CHUNKER_VERSION = "structured-v1"


def build_document_chunks(
    *,
    document_id,
    paper_id: str,
    topic_id,
    blocks: list[ParsedBlock],
    max_chars: int = 1400,
    overlap_chars: int = 180,
) -> list[DocumentChunk]:
    """Build bounded chunks from normalized PDF blocks.

    :return: Chunks that preserve page provenance.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if not 0 <= overlap_chars < max_chars:
        raise ValueError("overlap_chars must be smaller than max_chars")
    chunks: list[DocumentChunk] = []
    buffer: list[str] = []
    buffer_pages: list[int] = []

    def flush() -> None:
        if not buffer:
            return
        text = "\n".join(buffer).strip()
        chunks.append(
            DocumentChunk(
                document_id=document_id,
                paper_id=paper_id,
                topic_id=topic_id,
                chunk_index=len(chunks),
                text=text,
                page_start=min(buffer_pages),
                page_end=max(buffer_pages),
                chunker_version=CHUNKER_VERSION,
            )
        )

    def split_long_text(text: str) -> list[str]:
        """Split one oversized block into bounded character windows.

        :return: Text segments no longer than max_chars.
        """
        if len(text) <= max_chars:
            return [text]
        step = max_chars - overlap_chars
        return [text[start : start + max_chars] for start in range(0, len(text), step)]

    for block in blocks:
        for piece in split_long_text(block.text):
            candidate = "\n".join([*buffer, piece])
            if buffer and len(candidate) > max_chars:
                flush()
                tail = chunks[-1].text[-overlap_chars:] if overlap_chars else ""
                buffer = [tail] if tail else []
                buffer_pages = [chunks[-1].page_end] if tail else []
            buffer.append(piece)
            buffer_pages.append(block.page_number)
    flush()
    return chunks
```

当一个候选块超过 `max_chars` 时，示例仅将上一个块末尾的 `overlap_chars` 个字符带入下一个块，避免把整段内容重复写入索引。单个 PDF 文本块本身超过上限时，`split_long_text()` 先将其切成有界窗口，因此不会把超长块原样写入向量库。

为此函数添加测试，至少断言：

```python
assert chunks[0].page_start == 1
assert chunks[-1].page_end == 2
assert all(chunk.chunker_version == "structured-v1" for chunk in chunks)
assert all(chunk.text for chunk in chunks)
assert all(len(chunk.text) <= 1400 for chunk in chunks)
```

上述断言不能孤立放在交互式终端。创建 `tests/unit/test_chunking.py`，用最小的结构化块直接测试纯函数；它不读取 PDF、不连接 Chroma：

```python
from uuid import uuid4

import pytest

from app.domain.documents.models import ParsedBlock
from app.domain.knowledge.chunking import CHUNKER_VERSION, build_document_chunks


def test_chunking_preserves_pages_and_bounds_oversized_block() -> None:
    blocks = [
        ParsedBlock(text="A" * 900, page_number=1, block_order=0),
        ParsedBlock(text="B" * 900, page_number=2, block_order=1),
        ParsedBlock(text="C" * 2_000, page_number=2, block_order=2),
    ]

    chunks = build_document_chunks(
        document_id=uuid4(),
        paper_id="2401.00001",
        topic_id=uuid4(),
        blocks=blocks,
        max_chars=1_400,
        overlap_chars=180,
    )

    assert chunks[0].page_start == 1
    assert chunks[-1].page_end == 2
    assert all(chunk.chunker_version == CHUNKER_VERSION for chunk in chunks)
    assert all(0 < len(chunk.text) <= 1_400 for chunk in chunks)


def test_chunking_rejects_overlap_not_smaller_than_window() -> None:
    block = ParsedBlock(text="text", page_number=1, block_order=0)

    with pytest.raises(ValueError, match="overlap_chars"):
        build_document_chunks(
            document_id=uuid4(),
            paper_id="2401.00001",
            topic_id=uuid4(),
            blocks=[block],
            max_chars=100,
            overlap_chars=100,
        )
```

第二个测试刻意覆盖参数错误。先运行纯分块测试，再继续下载、解析和索引：

```bash
uv run pytest tests/unit/test_chunking.py -q
```

### 2.4 构建版本化索引

`application/indexing_service.py` 应演进为：

```text
选择 Topic / IndexVersion
→ 读取可用 DocumentChunk
→ 批量 Embedding
→ 写入 VectorStore
→ 标记索引版本 ready
```

索引写入必须携带：`paper_id`、`topic_id`、`document_id`、`section_title`、`page_start`、`page_end`、`chunker_version`、`index_version`。回答来源不只返回 URL，还应能定位页码和章节。

对索引失败使用阶段 2 的 `IndexJob`；不要通过同步 `/index/build` 处理全文。

IndexJob worker 的入口应只做四件事：读取 `IndexJob`、调用 `DocumentService.download_and_parse()` 与 `index_document()`、写入统计、更新终态。为 `tests/unit/test_index_execution_service.py` 使用 Fake Document Repository、Fake Embedding Client、Fake VectorStore 覆盖成功、解析失败和向量写入失败三条路径；其中失败路径必须保留原有 ready 索引版本。

#### 索引元数据与 Chroma 写入

将 `DocumentChunk` 转换为当前 `VectorStore` 所需数据时，不能丢失版本和页码：

```python
metadata = {
    "paper_id": chunk.paper_id,
    "topic_id": str(chunk.topic_id),
    "document_id": str(chunk.document_id),
    "page_start": chunk.page_start,
    "page_end": chunk.page_end,
    "chunker_version": chunk.chunker_version,
    "index_version": index_version,
}
```

索引服务的核心结构应类似：

```python
async def index_document(self, document_id, topic_id, index_version: str) -> int:
    document = self.document_repository.get_or_raise(document_id)
    blocks = self.parser.parse(document.file_path)
    chunks = build_document_chunks(
        document_id=document.id,
        paper_id=document.paper_id,
        topic_id=topic_id,
        blocks=blocks,
    )
    vectors = await self.embedding_client.embed_texts([chunk.text for chunk in chunks])
    self.vector_store.upsert_document_chunks(chunks, vectors, index_version=index_version)
    self.chunk_repository.replace_for_document(document_id, chunks)
    return len(chunks)
```

注意顺序：先成功写入新索引版本，再把该版本标记为 `ready`；不能先覆盖线上活跃索引、再祈祷任务成功。

### 2.5 检索策略的实验顺序

一次只改变一个变量，按此顺序比较：

1. 摘要语义检索基线。
2. 全文结构分块语义检索。
3. 增加专题、时间、分类、收藏状态等 metadata filter。
4. 加入关键词/BM25 候选，与向量候选融合。
5. 对 Top-K 候选使用重排序。
6. 仅在评测证据支持时加入 query rewrite 或上下文压缩。

不要先决定“必须迁移 pgvector/OpenSearch”。当前 `VectorStore` 是抽象端口；当 Chroma 无法满足 metadata filter、混合检索或容量指标时，再用基准测试决定替换方案。

---

## 3. 建立评测闭环

### 3.1 评测集格式

在仓库创建版本化、可审查的小型数据集，例如：

```text
evaluation/
├── datasets/
│   └── disinformation_v1.jsonl
├── scripts/
│   ├── run_retrieval_eval.py
│   └── run_answer_eval.py
└── reports/
    └── v0.4.0-baseline.md
```

每条 JSONL 至少包含：

```json
{
  "query_id": "disinfo-001",
  "question": "哪些论文讨论了生成式虚假信息的传播机制？",
  "relevant_paper_ids": ["..."],
  "relevant_chunk_ids": ["..."],
  "notes": "人工标注依据"
}
```

评测集问题应来自你真实会问的问题，不能只使用系统容易回答的关键词匹配问题。

#### 评测数据与脚本

创建 `evaluation/datasets/disinformation_v1.jsonl`：

```json
{"query_id":"d-001","question":"哪些论文讨论了生成式虚假信息的传播机制？","expected_behavior":"retrieve","relevant_paper_ids":["2401.00001"],"relevant_chunk_ids":["chunk-001"],"notes":"人工阅读摘要和全文后标注"}
{"query_id":"d-002","question":"当前论文库是否有量化跨平台治理效果的证据？","expected_behavior":"abstain","relevant_paper_ids":[],"relevant_chunk_ids":[],"notes":"应触发拒答或证据不足"}
```

创建 `evaluation/scripts/run_retrieval_eval.py`：

```python
import argparse
import json
from pathlib import Path

import httpx


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Calculate whether a retrieval query found labeled evidence.

    :return: Binary Recall@K score.
    """
    if not relevant:
        raise ValueError("Recall@K requires at least one relevant item")
    return float(bool(set(retrieved[:k]) & relevant))


def search(api_base: str, topic_id: str, question: str, index_version: str, top_k: int) -> list[dict]:
    """调用专题检索接口。

    :param api_base: PaperMind API 的基础地址。
    :param topic_id: 限定检索范围的专题标识。
    :param question: 评测问题文本。
    :param index_version: 要评估的索引版本。
    :param top_k: 返回的最大来源数量。
    :return: API 响应中的来源列表。
    """
    response = httpx.post(
        f"{api_base}/api/v1/topics/{topic_id}/search",
        json={"question": question, "top_k": top_k, "index_version": index_version},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["sources"]


def ask(api_base: str, topic_id: str, question: str, index_version: str) -> dict:
    """Call the grounded answer endpoint for an abstention case.

    :return: Answer payload containing insufficient_evidence.
    """
    response = httpx.post(
        f"{api_base}/api/v1/topics/{topic_id}/ask",
        json={"question": question, "index_version": index_version},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--topic-id", required=True)
    parser.add_argument("--index-version", required=True)
    parser.add_argument("--dataset", default="evaluation/datasets/disinformation_v1.jsonl")
    args = parser.parse_args()

    dataset = [json.loads(line) for line in Path(args.dataset).read_text(encoding="utf-8").splitlines() if line]
    retrieval_scores: list[float] = []
    abstention_scores: list[float] = []
    for item in dataset:
        if item["expected_behavior"] == "retrieve":
            results = search(args.api_base, args.topic_id, item["question"], args.index_version, top_k=5)
            ids = [result["paper_id"] for result in results]
            retrieval_scores.append(recall_at_k(ids, set(item["relevant_paper_ids"]), 5))
        elif item["expected_behavior"] == "abstain":
            answer = ask(args.api_base, args.topic_id, item["question"], args.index_version)
            abstention_scores.append(float(answer["insufficient_evidence"] is True))
        else:
            raise ValueError(f"unknown expected_behavior: {item['expected_behavior']}")
    report = {
        "dataset": args.dataset,
        "topic_id": args.topic_id,
        "index_version": args.index_version,
        "recall_at_5": sum(retrieval_scores) / len(retrieval_scores) if retrieval_scores else None,
        "abstention_accuracy": sum(abstention_scores) / len(abstention_scores) if abstention_scores else None,
        "retrieval_queries": len(retrieval_scores),
        "abstention_queries": len(abstention_scores),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
```

先在 Router 中实现 `POST /api/v1/topics/{topic_id}/search`，请求体包含 `question`、`top_k` 和 `index_version`，响应中返回 `sources` 数组；每项至少有 `paper_id` 与 `chunk_id`。同时实现 `POST /api/v1/topics/{topic_id}/ask`，其响应必须包含布尔字段 `insufficient_evidence`，仅在当前专题没有足够证据时为 `true`。检索样本和拒答样本是两类指标，不能用空的相关集合混算 Recall@K。前置条件：Router 已注册，目标专题已有完成的 IndexJob、指定索引版本和标注数据集，API 已启动且可访问。满足后执行评测：

```bash
uv run python evaluation/scripts/run_retrieval_eval.py \
  --topic-id <专题 UUID> \
  --index-version structured-v1-siliconflow-v1
```

固定的 `index_version` 必须间接固定 Embedding 模型和分块版本；上述参数和数据集路径都会被写入输出报告。否则同一指标无法复现。

### 3.2 指标与解释

| 层次 | 指标 | 回答的问题 |
| --- | --- | --- |
| 召回 | Recall@K | 相关证据是否进入候选集？ |
| 排序 | MRR、nDCG | 相关证据是否排在前面？ |
| 来源 | Citation Precision / 人工核对 | 回答引用是否支持对应说法？ |
| 回答 | Groundedness / 忠实度 | 回答是否超出给定证据？ |
| 拒答 | 拒答正确率 | 没有证据时是否诚实拒答？ |
| 工程 | P50/P95 延迟、单次成本、失败率 | 质量提升是否可接受？ |

自动指标不能替代人工核对。每个策略版本至少人工审查 10 条代表性问题，记录“找不到”“找错”“找到但回答歪曲”“正确拒答”等失败类型。

### 3.3 发布门禁

每次改动 Chunk、Embedding、检索、重排序或 Prompt 时：

1. 固定数据集、模型版本和参数。
2. 运行评测并保存机器可读结果与简短报告。
3. 比较上一基线的质量、延迟、成本。
4. 质量无提升或成本明显不可接受时不设为默认。
5. 将关键失败样例加入回归测试。

---

## 4. API 与前端验收

### 全文链路闭环清单

在公开全文状态和问答前，以下实现文件与测试文件必须存在。它们将阶段 2 的 IndexJob 接到阶段 3 的真实全文流水线：

```text
app/infrastructure/persistence/document_repository.py
app/application/index_execution_service.py
app/infrastructure/tasks/index_tasks.py
app/api/v1/documents.py
app/api/v1/search.py
tests/unit/test_index_execution_service.py
tests/integration/test_document_repository.py
tests/api/test_document_status.py
tests/api/test_search.py
```

`test_index_execution_service.py` 使用 Fake Parser、Embedding Client、VectorStore，断言新版本成功后才成为 ready；`test_document_repository.py` 验证版本化 Chunk 唯一约束；`test_search.py` 验证专题过滤、页码来源和 `insufficient_evidence`。运行：

```bash
uv run pytest tests/unit/test_document_service.py tests/unit/test_chunking.py tests/unit/test_index_execution_service.py tests/api/test_document_status.py tests/api/test_search.py -q
```

#### IndexJob worker 与全文 API 实现包

创建 `app/application/index_execution_service.py`：它接收 IndexJob、Document Repository、DocumentService、Embedding Client、VectorStore 和 IndexVersion Repository。执行顺序固定为：原子领取 Job → 读取目标专题的 Document → 下载/解析 → 保存 Chunk → 写入一个尚未 ready 的 `index_version` → 全部写入成功后标记 ready → Job 成功；任何异常只将本次 Job 和候选版本标记 failed，绝不覆盖旧 ready 版本。

```python
from uuid import UUID

from app.domain.knowledge.chunking import build_document_chunks


class IndexExecutionService:
    """Build a candidate index version without replacing the ready version early."""

    def __init__(self, documents, document_service, chunk_repository, embeddings, vector_store, versions, jobs=None) -> None:
        self.documents = documents
        self.document_service = document_service
        self.chunk_repository = chunk_repository
        self.embeddings = embeddings
        self.vector_store = vector_store
        self.versions = versions
        self.jobs = jobs

    async def build(self, document_id: UUID, topic_id: UUID, target_version: str) -> int:
        """Build and then atomically publish one candidate index version.

        :return: Number of indexed chunks.
        """
        self.versions.begin(topic_id, target_version)
        try:
            document = self.documents.get_for_topic(topic_id, document_id)
            if document is None:
                raise LookupError("document not found in topic")
            blocks = self.document_service.download_and_parse(document.id)
            chunks = build_document_chunks(document_id=document.id, paper_id=document.paper_id, topic_id=topic_id, blocks=blocks)
            vectors = await self.embeddings.embed_texts([chunk.text for chunk in chunks])
            self.vector_store.upsert_document_chunks(chunks, vectors, index_version=target_version)
            self.chunk_repository.replace_for_document(document.id, chunks)
            self.versions.mark_ready(topic_id, target_version)
            return len(chunks)
        except Exception:
            self.versions.mark_failed(topic_id, target_version)
            raise

    async def execute(self, job_id: UUID) -> None:
        """Claim an IndexJob and preserve the previous ready version on failure."""
        if self.jobs is None:
            raise RuntimeError("IndexExecutionService requires an IndexJob repository for execute()")
        job = self.jobs.mark_running(job_id)
        if job is None:
            return
        try:
            if job.document_id is None:
                raise ValueError("full-text IndexJob requires document_id")
            count = await self.build(job.document_id, job.topic_id, job.target_index_version)
            self.jobs.finish_success(job.id, indexed_count=count)
        except Exception as exc:
            self.jobs.finish_failure(job.id, str(exc))
            raise
```

创建 `app/infrastructure/tasks/index_tasks.py`：

```python
import asyncio
from uuid import UUID

from app.core.container import AppContainer
from app.infrastructure.tasks.celery_app import celery_app


@celery_app.task(bind=True, max_retries=2)
def run_index_job(self, job_id: str) -> None:
    """Run one queued IndexJob through the application service.

    :return: None.
    """
    asyncio.run(AppContainer().index_execution_service.execute(UUID(job_id)))
```

阶段 3 的 worker 必须同时发现采集、全文索引和订阅扫描任务。以下是 `app/infrastructure/tasks/celery_app.py` 与 `app/infrastructure/tasks/dispatcher.py` 的**完整替换版本**；它们取代阶段 2 的只含采集任务版本：

```python
# app/infrastructure/tasks/celery_app.py
from celery import Celery

from app.core.config import Settings

settings = Settings()
celery_app = Celery(
    "papermind",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.infrastructure.tasks.collection_tasks",
        "app.infrastructure.tasks.index_tasks",
        "app.infrastructure.tasks.schedules",
    ],
)
celery_app.conf.update(
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=15 * 60,
)
```

```python
# app/infrastructure/tasks/dispatcher.py
from uuid import UUID

class CeleryTaskDispatcher:
    """Dispatch application jobs without leaking Celery into services."""

    def dispatch_collection(self, job_id: UUID) -> None:
        from app.infrastructure.tasks.collection_tasks import run_collection_job

        run_collection_job.delay(str(job_id))

    def dispatch_index(self, job_id: UUID) -> None:
        from app.infrastructure.tasks.index_tasks import run_index_job

        run_index_job.delay(str(job_id))
```

创建 `app/api/v1/documents.py`，只提供 `GET /topics/{topic_id}/documents/{document_id}` 的状态与来源元数据；创建 `app/api/v1/search.py`，提供 `POST /topics/{topic_id}/search` 和 `POST /topics/{topic_id}/ask`。Search 响应返回 `sources`，Ask 响应必须返回 `answer`、`sources`、`insufficient_evidence` 和 `index_version`。所有 Router 先验证 Document/Chunk/Topic 属于路径专题，再调用服务。

`tests/unit/test_index_execution_service.py` 使用 Fake DocumentRepository、Fake EmbeddingClient、Fake VectorStore，覆盖成功、解析失败、向量写入失败和“旧 ready 版本保持不变”。`tests/api/test_document_status.py` 覆盖跨专题 Document 返回 `404`；`tests/api/test_search.py` 覆盖来源页码、专题过滤与证据不足。带 PostgreSQL 的 Repository 测试标记 `integration`，Chroma 与模型客户端仍使用 Fake。

创建 `tests/unit/test_index_execution_service.py`：

```python
from uuid import uuid4

import pytest

from app.application.index_execution_service import IndexExecutionService
from app.domain.documents.models import Document, ParsedBlock


class FakeVersions:
    def __init__(self) -> None:
        self.ready = "old-v1"

    def begin(self, topic_id, version):
        return version

    def mark_ready(self, topic_id, version):
        self.ready = version

    def mark_failed(self, topic_id, version):
        return None


class FailingVectorStore:
    def upsert_document_chunks(self, chunks, vectors, *, index_version):
        raise RuntimeError("chroma unavailable")


class SuccessfulVectorStore:
    def __init__(self) -> None:
        self.writes = []

    def upsert_document_chunks(self, chunks, vectors, *, index_version):
        self.writes.append((chunks, vectors, index_version))


class FakeDocuments:
    def get_for_topic(self, topic_id, document_id):
        return Document(
            id=document_id,
            paper_id="2401.00001",
            source_url="https://example.test/paper.pdf",
            file_path="data/papers/2401.00001.pdf",
        )


class FakeDocumentService:
    def download_and_parse(self, document_id):
        return [ParsedBlock(text="evidence", page_number=1, block_order=0)]


class FailingDocumentService:
    def download_and_parse(self, document_id):
        raise ValueError("invalid PDF")


class FakeChunks:
    def __init__(self) -> None:
        self.replacements = []

    def replace_for_document(self, document_id, chunks):
        self.replacements.append((document_id, chunks))


class FakeEmbeddings:
    async def embed_texts(self, texts):
        return [[0.0, 1.0] for _ in texts]


@pytest.mark.asyncio
async def test_failed_candidate_does_not_replace_ready_version() -> None:
    versions = FakeVersions()
    service = IndexExecutionService(FakeDocuments(), FakeDocumentService(), FakeChunks(), FakeEmbeddings(), FailingVectorStore(), versions)

    with pytest.raises(RuntimeError, match="chroma unavailable"):
        await service.build(document_id=uuid4(), topic_id=uuid4(), target_version="candidate-v2")

    assert versions.ready == "old-v1"


@pytest.mark.asyncio
async def test_successful_candidate_becomes_ready_after_vector_write() -> None:
    versions, chunks, vectors = FakeVersions(), FakeChunks(), SuccessfulVectorStore()
    service = IndexExecutionService(FakeDocuments(), FakeDocumentService(), chunks, FakeEmbeddings(), vectors, versions)

    assert await service.build(uuid4(), uuid4(), "candidate-v2") == 1
    assert versions.ready == "candidate-v2"
    assert len(vectors.writes) == 1
    assert len(chunks.replacements) == 1


@pytest.mark.asyncio
async def test_parse_failure_keeps_old_ready_version() -> None:
    versions = FakeVersions()
    service = IndexExecutionService(FakeDocuments(), FailingDocumentService(), FakeChunks(), FakeEmbeddings(), SuccessfulVectorStore(), versions)

    with pytest.raises(ValueError, match="invalid PDF"):
        await service.build(uuid4(), uuid4(), "candidate-v2")

    assert versions.ready == "old-v1"
```

以上就是 `tests/unit/test_index_execution_service.py` 的完整三个关键分支；不要在测试中启动 Chroma 或调用模型 API。这三个分支共同验证“候选版本失败不替换旧索引”的发布不变量。

本阶段不应暴露原始 Chunk 管理接口给普通用户。用户可见能力是：

- 专题论文详情显示“全文可用 / 解析失败 / 仅摘要”状态。
- 回答来源显示论文名、章节、页码、片段和原文链接。
- 用户可选择仅检索全文、允许摘要降级或按时间/分类过滤。
- 索引任务状态通过 Job API 查询，不阻塞页面。

当会话资源已在阶段 1 完成时，回答通过 `POST /api/v1/conversations/{id}/messages` 返回；来源是消息响应的一部分，并保存所用 `index_version`。

---

### 回答来源渲染

后端返回来源 DTO 时应包含页码区间：

```python
class Source(BaseModel):
    """定义回答中单条可追溯来源。"""

    title: str
    url: str
    pdf_url: str | None
    section_title: str | None
    page_start: int | None
    page_end: int | None
    text: str
    index_version: str
```

React 中不要只显示“来源 1”；至少显示：

```tsx
<a href={source.pdf_url ?? source.url} target="_blank" rel="noreferrer">
  {source.title}（第 {source.page_start}–{source.page_end} 页）
</a>
<blockquote>{source.text}</blockquote>
```

页码链接能否精确跳转取决于 PDF 托管方式；即使不能加 `#page=`，也要将页码显示给用户。

## 5. 验证、完成定义与常见错误

### 自动化与人工验证

- PDF 下载、哈希、解析失败、页码保留、结构分块均有单元测试。
- 使用至少 30 条标注问题运行检索评测。
- 至少一个专题通过全文问答返回可跳转的页码来源。
- 上游模型不可用时，界面仍展示已检索到的证据并说明生成暂不可用。
- 索引可通过版本号重建；旧版本不会被静默覆盖。

### `v0.4.0` 完成定义

- 全文、章节/页码来源、索引版本和评测报告均可追溯。
- 当前默认策略有可复现的质量、延迟和成本基线。
- 论文下载/解析失败不损坏已有摘要检索。
- 检索与回答能区分“没有相关证据”和“模型不能可靠概括证据”。
- README 与验收报告记录当前指标、数据集范围和已知限制。
- 本文“混合检索、重排序与拒答闭环”补充中的 IndexJob worker、策略回归与 `answer_status` 均已完成并通过测试。

### 常见错误

| 问题 | 正确处理 |
| --- | --- |
| 将整篇 PDF 直接作为一个向量 | 按结构分块并保留页码、章节、顺序。 |
| 不保存解析器/分块/索引版本 | 无法复现结果；所有产物都应带版本。 |
| 只看最终回答是否“像对的” | 先评估检索，再评估引用和回答忠实度。 |
| 失败 PDF 直接删除论文 | 保留元数据、来源和失败状态，允许摘要降级。 |
| 同时修改模型、Chunk、Prompt、重排 | 无法判断性能变化原因；一次只改变一个变量。 |

### 推荐提交边界

1. `feat: 增加论文全文下载与解析链路`
2. `feat: 引入结构化分块和版本化索引`
3. `feat: 支持全文来源定位与索引任务`
4. `test: 添加检索评测集与回归脚本`

阶段结束后，你应能用评测报告而非直觉说明：当前 RAG 检索到了什么、为什么比旧策略好、代价是什么、哪些问题仍不能回答。

---

## 补充：混合检索、重排序与拒答闭环

阶段 3 先实现实际 IndexJob worker：它只接收已解析 Document 的标识和目标索引版本，读取稳定的 DocumentChunk，调用 Embedding Provider，并以 `chunk_id` 幂等写入向量库。成功或失败均写回阶段 2 的 IndexJob；在一个 `document_id + index_version` 已有活跃任务时复用该任务。这样阶段 2 的契约不会依赖尚未存在的全文模型。

全文向量检索是基线，而不是默认终点。为使路线中的混合检索、重排序和拒答成为可验证能力，新增两个端口：`KeywordSearcher.search(query, filters, limit)` 返回按关键词匹配的 Chunk 候选；`Reranker.rank(query, candidates)` 返回带重排分数的候选。二者均由应用层调用，不让 Router 直接接触具体搜索引擎或模型 SDK。

策略按固定顺序实现和比较：先以同一评测集记录语义检索基线；再将语义候选与关键词候选按 `chunk_id` 去重、保留各自原始分数；最后仅对合并后的 Top-N 调用重排序器。每一次比较固定数据快照、Chunker、Embedding、关键词实现、Reranker、`top_k`、超时和成本上限，并输出机器可读报告。

拒答必须由可测试规则决定，而不是由提示词碰运气。应用服务在生成前检查：候选是否为空、最高证据分是否低于经评测确定的阈值、来源是否覆盖回答中的关键主张。任一条件不满足时返回固定的“证据不足”状态和已有来源，不调用 LLM。API 响应增加 `answer_status`，取值为 `answered`、`insufficient_evidence` 或 `generation_unavailable`；前端按该状态展示原因和可打开的来源。

发布默认策略的门槛是：相对上一个默认版本，Recall@K、MRR、引用正确性和拒答正确率均有记录；延迟与成本未超过已写明预算；至少十条人工审查样本与全部失败样本可追溯。没有达到门槛的策略保留为实验配置，不替换线上默认值。
