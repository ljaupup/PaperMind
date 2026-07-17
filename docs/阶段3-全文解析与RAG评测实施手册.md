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
        """为专题可见论文创建全文元数据。

        :param topic_id: 论文所属专题的 ID。
        :param paper_id: 全局论文 ID。
        :param source_url: 可下载全文的来源地址。
        :return: 已保存、等待后台索引的文档。
        :raises LookupError: 论文不属于指定专题时抛出。
        """
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

前置条件：`test_document_service.py` 已保存，测试使用 Fake 下载器和解析器，不连接外部服务。满足后运行：

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
    op.add_column(
        "papers",
        sa.Column("categories", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
    )
    op.create_index("ix_papers_categories", "papers", ["categories"], postgresql_using="gin")
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
    op.drop_index("ix_papers_categories", table_name="papers")
    op.drop_column("papers", "categories")
```

`papers.categories` 在阶段 3 才加入，因为分类过滤属于检索优化，不改变阶段 1 的最小持久化边界。同步扩展 `Paper` 和 `PaperRecord`：

用下面内容完整替换阶段 1 的 `app/domain/models.py`：

```python
from datetime import datetime

from pydantic import BaseModel, Field


class Paper(BaseModel):
    """表示跨专题复用的全局论文元数据。"""

    paper_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1)
    abstract: str = ""
    authors: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    url: str
    pdf_url: str | None = None
    published_at: datetime | None = None
    file_path: str | None = None
    file_hash: str | None = None
    parse_status: str = "metadata_only"


class PaperChunk(BaseModel):
    """表示阶段 1 索引中保留的摘要片段。"""

    chunk_id: str
    paper_id: str
    title: str
    url: str
    pdf_url: str | None = None
    page: int | None = None
    text: str = Field(min_length=1)
```

用下面内容完整替换 `app/infrastructure/persistence/models.py`：

```python
from datetime import datetime

from sqlalchemy import DateTime, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """作为 PaperMind 全部 SQLAlchemy ORM 记录的基类。"""


class PaperRecord(Base):
    """将全局论文元数据映射到 PostgreSQL。"""

    __tablename__ = "papers"

    paper_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    abstract: Mapped[str] = mapped_column(Text, nullable=False, default="")
    authors: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    categories: Mapped[list[str]] = mapped_column(
        ARRAY(String()),
        nullable=False,
        default=list,
        server_default="{}",
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    pdf_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    file_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    file_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    parse_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="metadata_only",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
```

用下面内容完整替换 `app/infrastructure/arxiv/collector.py`，使分类过滤具有真实数据来源：

```python
import asyncio

import arxiv

from app.domain.models import Paper


_CLIENT = arxiv.Client(page_size=10, delay_seconds=4.0, num_retries=1)


def clean_text(value: str | None) -> str:
    """压缩外部元数据中的连续空白。

    :param value: 可能为空的原始文本。
    :return: 去除首尾和连续空白的文本。
    """
    return " ".join(value.split()) if value else ""


def result_to_paper(result: arxiv.Result) -> Paper:
    """将 arXiv SDK 结果转换为内部论文模型。

    :param result: arXiv SDK 返回的单条结果。
    :return: 清洗后的论文领域对象。
    """
    return Paper(
        paper_id=result.get_short_id(),
        title=clean_text(result.title),
        abstract=clean_text(result.summary),
        authors=[
            name
            for author in result.authors
            if (name := clean_text(author.name))
        ],
        categories=list(getattr(result, "categories", []) or []),
        url=result.entry_id,
        pdf_url=result.pdf_url,
        published_at=result.published,
    )


def _collect_sync(query: str, max_results: int) -> list[Paper]:
    """同步调用 arXiv SDK 并转换结果。

    :param query: arXiv 检索表达式。
    :param max_results: 最多返回的论文数量。
    :return: 按提交时间倒序的论文列表。
    :raises RuntimeError: arXiv 返回限流时抛出。
    :raises arxiv.HTTPError: 其他 arXiv HTTP 错误时抛出。
    """
    search = arxiv.Search(
        query=query,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending,
    )
    try:
        return [result_to_paper(result) for result in _CLIENT.results(search)]
    except arxiv.HTTPError as exc:
        if exc.status == 429:
            raise RuntimeError("arXiv 限流；等待至少 60 秒后仅重试一次") from exc
        raise


async def collect_arxiv(query: str, max_results: int) -> list[Paper]:
    """在工作线程中执行同步 arXiv SDK 查询。

    :param query: arXiv 检索表达式。
    :param max_results: 最多返回的论文数量。
    :return: 论文领域对象列表。
    """
    return await asyncio.to_thread(_collect_sync, query, max_results)
```

未知或缺失分类返回空列表，不把 `None` 写入非空列。这样日期、分类和收藏过滤都有真实数据来源。

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

上文给出的唯一 `0003_documents_and_evaluations.py` 已经完整创建并回滚 `index_versions`；不要再次插入同名建表或索引片段，否则迁移会因对象重复而失败。

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
    url = os.environ["PAPER_MIND_TEST_POSTGRES_URL"]
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

前置条件：PostgreSQL 已启动并通过健康检查，环境变量指向专用空测试库；该测试会自行建表和清理。满足后运行：

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
        """在迁移到全文索引时保留阶段 1 的摘要片段。

        :param chunks: 要写入或更新的摘要片段。
        :param embeddings: 与片段一一对应的向量。
        :return: None。
        :raises ValueError: 片段数量与向量数量不一致时抛出。
        """
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
        paper_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        where_items: list[dict[str, Any]] = [
            {"kind": {"$eq": "document"}},
            {"topic_id": {"$eq": str(topic_id)}},
            {"index_version": {"$eq": index_version}},
        ]
        if paper_ids is not None:
            if not paper_ids:
                return []
            where_items.append({"paper_id": {"$in": paper_ids}})
        result = self.collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            where={"$and": where_items},
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
import re
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

第二个测试刻意覆盖参数错误。前置条件：分块实现和该测试文件已保存；它不连接外部服务。先运行纯分块测试，再继续下载、解析和索引：

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

`test_index_execution_service.py` 使用 Fake Parser、Embedding Client、VectorStore，断言新版本成功后才成为 ready；`test_document_repository.py` 验证版本化 Chunk 唯一约束；`test_search.py` 验证专题过滤、页码来源和 `insufficient_evidence`。前置条件：本节列出的 Service、Router 和测试文件均已保存，当前命令不运行标记为 integration 的 PostgreSQL 测试。满足后运行：

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
        """执行索引任务，并在失败时保留旧的可用版本。

        :param job_id: 要领取并执行的索引任务 ID。
        :return: None。
        :raises RuntimeError: 未配置索引任务仓储时抛出。
        """
        if self.jobs is None:
            raise RuntimeError("IndexExecutionService requires an IndexJob repository for execute()")
        job = self.jobs.mark_running(job_id)
        if job is None:
            return
        try:
            if job.document_id is None:
                raise ValueError("full-text IndexJob requires document_id")
            await self.build(job.document_id, job.topic_id, job.target_index_version)
            self.jobs.finish_success(job.id)
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

本学习路线不要求持久化会话。回答继续通过 `POST /api/v1/topics/{topic_id}/ask` 返回，来源和本次使用的 `index_version` 都是响应的一部分；阶段 3 不依赖 `Conversation` 或 `Message` 表。

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

### 混合检索领域契约与可运行基线

创建 `app/domain/knowledge/retrieval.py`。语义距离、关键词分和最终重排分分开保存，评测时才能判断改进来自哪里：

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class SearchFilters:
    """定义专题检索允许使用的元数据过滤条件。"""

    published_from: datetime | None = None
    categories: tuple[str, ...] = ()
    favorite_only: bool = False


@dataclass(frozen=True)
class RetrievalCandidate:
    """保存候选来源及各阶段的独立分数。"""

    paper_id: str
    title: str
    url: str
    pdf_url: str | None
    chunk_id: UUID
    section_title: str | None
    page_start: int
    page_end: int
    text: str
    semantic_distance: float | None = None
    keyword_score: float | None = None
    rerank_score: float = 0.0


class KeywordSearcher(Protocol):
    """定义关键词候选和过滤范围查询。"""

    def allowed_paper_ids(self, topic_id: UUID, filters: SearchFilters) -> set[str]:
        """查询符合专题和元数据过滤条件的论文 ID。

        :param topic_id: 当前专题 ID。
        :param filters: 日期、分类和收藏过滤条件。
        :return: 允许进入召回阶段的论文 ID 集合。
        """
        ...

    def paper_details(self, paper_ids: set[str]) -> dict[str, tuple[str, str, str | None]]:
        """查询来源展示所需的论文元数据。

        :param paper_ids: 要查询的论文 ID 集合。
        :return: 从论文 ID 到标题、原文链接和 PDF 链接的映射。
        """
        ...

    def search(
        self,
        topic_id: UUID,
        query: str,
        filters: SearchFilters,
        limit: int,
    ) -> list[RetrievalCandidate]:
        """按关键词召回专题内的全文片段。

        :param topic_id: 当前专题 ID。
        :param query: 用户检索问题。
        :param filters: 元数据过滤条件。
        :param limit: 最多返回的候选数量。
        :return: 按关键词分降序排列的候选。
        """
        ...


class Reranker(Protocol):
    """定义合并语义和关键词候选的重排序操作。"""

    def rank(
        self,
        semantic: list[RetrievalCandidate],
        keyword: list[RetrievalCandidate],
        limit: int,
    ) -> list[RetrievalCandidate]:
        """融合两路候选并返回最终排序。

        :param semantic: 按向量距离排列的候选。
        :param keyword: 按关键词分排列的候选。
        :param limit: 最终保留数量。
        :return: 带独立原始分和最终重排分的候选。
        """
        ...
```

创建 `app/infrastructure/persistence/keyword_searcher.py`。这是可解释的 PostgreSQL 基线；数据规模扩大后可用 PostgreSQL 全文索引替换 `ILIKE`，但端口不变：

```python
from uuid import UUID

from sqlalchemy import or_, select

from app.domain.knowledge.retrieval import RetrievalCandidate, SearchFilters
from app.infrastructure.persistence.document_models import DocumentChunkRecord
from app.infrastructure.persistence.models import PaperRecord
from app.infrastructure.persistence.topic_models import TopicPaperRecord


def keyword_terms(query: str, limit: int = 20) -> list[str]:
    """将中英文问题转换为可用于基线召回的去重词项。

    :param query: 用户输入的检索问题。
    :param limit: 最多保留的词项数量。
    :return: 英文单词或中文双字词项的去重列表。
    """
    terms: list[str] = []
    for part in re.findall(r"[a-z0-9][a-z0-9_.-]*|[\u4e00-\u9fff]+", query.lower()):
        if "\u4e00" <= part[0] <= "\u9fff" and len(part) > 2:
            terms.extend(part[index : index + 2] for index in range(len(part) - 1))
        else:
            terms.append(part)
    return list(dict.fromkeys(terms))[:limit]


class PostgresKeywordSearcher:
    """使用 PostgreSQL 过滤并召回关键词片段。"""

    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    def allowed_paper_ids(self, topic_id: UUID, filters: SearchFilters) -> set[str]:
        """查询符合专题和元数据过滤条件的论文 ID。

        :param topic_id: 当前专题 ID。
        :param filters: 日期、分类和收藏过滤条件。
        :return: 允许进入召回阶段的论文 ID 集合。
        """
        statement = (
            select(PaperRecord.paper_id)
            .join(TopicPaperRecord, TopicPaperRecord.paper_id == PaperRecord.paper_id)
            .where(TopicPaperRecord.topic_id == topic_id)
        )
        if filters.published_from is not None:
            statement = statement.where(PaperRecord.published_at >= filters.published_from)
        if filters.categories:
            statement = statement.where(PaperRecord.categories.overlap(list(filters.categories)))
        if filters.favorite_only:
            statement = statement.where(TopicPaperRecord.is_favorite.is_(True))
        with self.session_factory() as session:
            return set(session.scalars(statement).all())

    def paper_details(self, paper_ids: set[str]) -> dict[str, tuple[str, str, str | None]]:
        """查询来源展示所需的论文元数据。

        :param paper_ids: 要查询的论文 ID 集合。
        :return: 从论文 ID 到标题、原文链接和 PDF 链接的映射。
        """
        if not paper_ids:
            return {}
        statement = select(
            PaperRecord.paper_id,
            PaperRecord.title,
            PaperRecord.url,
            PaperRecord.pdf_url,
        ).where(PaperRecord.paper_id.in_(paper_ids))
        with self.session_factory() as session:
            rows = session.execute(statement).all()
        return {
            paper_id: (title, url, pdf_url)
            for paper_id, title, url, pdf_url in rows
        }

    def search(
        self,
        topic_id: UUID,
        query: str,
        filters: SearchFilters,
        limit: int,
    ) -> list[RetrievalCandidate]:
        """按词项覆盖率召回专题内的全文片段。

        :param topic_id: 当前专题 ID。
        :param query: 用户检索问题。
        :param filters: 元数据过滤条件。
        :param limit: 最多返回的候选数量。
        :return: 按关键词覆盖率降序排列的候选。
        """
        tokens = keyword_terms(query)
        allowed = self.allowed_paper_ids(topic_id, filters)
        if not tokens or not allowed:
            return []
        details = self.paper_details(allowed)
        statement = (
            select(DocumentChunkRecord)
            .where(
                DocumentChunkRecord.topic_id == topic_id,
                DocumentChunkRecord.paper_id.in_(allowed),
                or_(*(DocumentChunkRecord.text.ilike(f"%{token}%") for token in tokens)),
            )
            .limit(limit * 4)
        )
        with self.session_factory() as session:
            records = session.scalars(statement).all()
        candidates = [
            RetrievalCandidate(
                paper_id=record.paper_id,
                title=details[record.paper_id][0],
                url=details[record.paper_id][1],
                pdf_url=details[record.paper_id][2],
                chunk_id=record.id,
                section_title=record.section_title,
                page_start=record.page_start,
                page_end=record.page_end,
                text=record.text,
                keyword_score=sum(token in record.text.lower() for token in tokens) / len(tokens),
            )
            for record in records
        ]
        return sorted(candidates, key=lambda item: item.keyword_score or 0.0, reverse=True)[:limit]
```

`allowed_paper_ids()` 先应用专题、日期、分类和收藏条件；两路召回使用同一集合，避免关键词路径和向量路径得到不同的数据边界。

创建 `app/application/reranking.py`。第一个可运行版本使用 Reciprocal Rank Fusion；它不需要外部模型，适合先建立稳定基线，后续可在同一端口下替换为 cross-encoder：

```python
from dataclasses import replace

from app.domain.knowledge.retrieval import RetrievalCandidate


class ReciprocalRankReranker:
    """使用倒数排名融合语义与关键词候选。"""

    def __init__(self, rank_constant: int = 60) -> None:
        self.rank_constant = rank_constant

    def rank(
        self,
        semantic: list[RetrievalCandidate],
        keyword: list[RetrievalCandidate],
        limit: int,
    ) -> list[RetrievalCandidate]:
        """按片段 ID 去重并计算倒数排名融合分。

        :param semantic: 按向量距离排列的候选。
        :param keyword: 按关键词分排列的候选。
        :param limit: 最终保留数量。
        :return: 按融合分降序排列的去重候选。
        """
        merged: dict[object, RetrievalCandidate] = {}
        scores: dict[object, float] = {}
        for candidates in (semantic, keyword):
            for rank, candidate in enumerate(candidates, start=1):
                previous = merged.get(candidate.chunk_id)
                if previous is None:
                    merged[candidate.chunk_id] = candidate
                else:
                    merged[candidate.chunk_id] = replace(
                        previous,
                        semantic_distance=(
                            previous.semantic_distance
                            if previous.semantic_distance is not None
                            else candidate.semantic_distance
                        ),
                        keyword_score=(
                            previous.keyword_score
                            if previous.keyword_score is not None
                            else candidate.keyword_score
                        ),
                    )
                scores[candidate.chunk_id] = scores.get(candidate.chunk_id, 0.0) + 1 / (
                    self.rank_constant + rank
                )
        ranked = [replace(item, rerank_score=scores[item.chunk_id]) for item in merged.values()]
        return sorted(ranked, key=lambda item: item.rerank_score, reverse=True)[:limit]
```

创建 `app/application/hybrid_retrieval_service.py`，并从阶段 3 起用它替换摘要时代的 `RetrievalService`：

```python
from dataclasses import dataclass
from uuid import UUID

from app.domain.knowledge.retrieval import RetrievalCandidate, SearchFilters


@dataclass(frozen=True)
class SearchResult:
    """保存检索来源和实际使用的索引版本。"""

    sources: list[RetrievalCandidate]
    index_version: str


class HybridRetrievalService:
    """在统一过滤范围内融合向量与关键词候选。"""

    def __init__(self, embeddings, vector_store, versions, keyword_searcher, reranker) -> None:
        self.embeddings = embeddings
        self.vector_store = vector_store
        self.versions = versions
        self.keyword_searcher = keyword_searcher
        self.reranker = reranker

    async def search(
        self,
        *,
        topic_id: UUID,
        question: str,
        top_k: int,
        index_version: str | None = None,
        filters: SearchFilters | None = None,
    ) -> SearchResult:
        """执行专题范围内的混合检索。

        :param topic_id: 当前专题 ID。
        :param question: 用户问题。
        :param top_k: 最终返回的来源数量。
        :param index_version: 可选的固定索引版本。
        :param filters: 可选的日期、分类和收藏过滤条件。
        :return: 混合重排后的来源和实际索引版本。
        """
        active_filters = filters or SearchFilters()
        version = index_version
        if version is None:
            ready = self.versions.get_ready(topic_id)
            if ready is None:
                return SearchResult([], "")
            version = ready.version
        allowed = self.keyword_searcher.allowed_paper_ids(topic_id, active_filters)
        if not allowed:
            return SearchResult([], version)
        details = self.keyword_searcher.paper_details(allowed)
        embedding = (await self.embeddings.embed_texts([question]))[0]
        rows = self.vector_store.query_document_chunks(
            embedding,
            topic_id=topic_id,
            index_version=version,
            top_k=min(top_k * 5, 100),
            paper_ids=sorted(allowed),
        )
        semantic = [
            RetrievalCandidate(
                paper_id=row["paper_id"],
                title=details[row["paper_id"]][0],
                url=details[row["paper_id"]][1],
                pdf_url=details[row["paper_id"]][2],
                chunk_id=UUID(row["chunk_id"]),
                section_title=row["section_title"] or None,
                page_start=int(row["page_start"]),
                page_end=int(row["page_end"]),
                text=row["text"],
                semantic_distance=float(row["score"]),
            )
            for row in rows
        ]
        keyword = self.keyword_searcher.search(topic_id, question, active_filters, top_k * 5)
        return SearchResult(self.reranker.rank(semantic, keyword, top_k), version)
```

Chroma 返回的是 cosine distance，数值越小越相似；本实现保留原始距离，但融合时只使用候选排名，避免误把距离当成“越大越好”的相关分。`paper_ids` 直接进入 Chroma 的 `where` 条件，不是先取全局 Top-N 再在内存中过滤；否则不属于当前专题的候选可能挤掉全部合法结果。

### 拒答状态和模型降级

用下面版本替换 `app/application/rag_service.py`。阈值来自固定评测集，不能按单个问题临时调整：

```python
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.domain.knowledge.retrieval import RetrievalCandidate, SearchFilters


class AnswerStatus(StrEnum):
    """定义问答端点可解释的结果状态。"""

    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    GENERATION_UNAVAILABLE = "generation_unavailable"


@dataclass(frozen=True)
class AskResult:
    """保存回答、来源、状态和索引版本。"""

    answer: str
    sources: list[RetrievalCandidate]
    answer_status: AnswerStatus
    index_version: str

    @property
    def insufficient_evidence(self) -> bool:
        """返回结果是否因为证据不足而拒答。

        :return: 状态为证据不足时返回 True。
        """
        return self.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE


class RAGService:
    """根据检索证据生成回答，并对不足或模型失败显式降级。"""

    def __init__(self, retrieval, llm, min_rerank_score: float) -> None:
        self.retrieval = retrieval
        self.llm = llm
        self.min_rerank_score = min_rerank_score

    async def ask(
        self,
        *,
        topic_id: UUID,
        question: str,
        top_k: int,
        index_version: str | None = None,
        filters: SearchFilters | None = None,
    ) -> AskResult:
        """检索证据并返回可解释的回答状态。

        :param topic_id: 当前专题 ID。
        :param question: 用户问题。
        :param top_k: 最多使用的来源数量。
        :param index_version: 可选的固定索引版本。
        :param filters: 可选的元数据过滤条件。
        :return: 回答、来源、状态和索引版本。
        """
        result = await self.retrieval.search(
            topic_id=topic_id,
            question=question,
            top_k=top_k,
            index_version=index_version,
            filters=filters,
        )
        if not result.sources or result.sources[0].rerank_score < self.min_rerank_score:
            return AskResult(
                "证据不足，无法回答。",
                result.sources,
                AnswerStatus.INSUFFICIENT_EVIDENCE,
                result.index_version,
            )
        try:
            answer = await self.llm.generate(question, [source.text for source in result.sources])
        except Exception:
            return AskResult(
                "已找到相关证据，但生成服务暂不可用。",
                result.sources,
                AnswerStatus.GENERATION_UNAVAILABLE,
                result.index_version,
            )
        return AskResult(answer, result.sources, AnswerStatus.ANSWERED, result.index_version)
```

用下面内容完整替换阶段 2 的 `app/core/config.py`，避免只手工插入一个字段后遗漏 Redis 或 Provider 配置：

```python
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

load_dotenv()


def env(name: str, default: str = "") -> str:
    """读取环境变量并在缺失时返回默认值。

    :param name: 环境变量名称。
    :param default: 变量缺失时使用的值。
    :return: 环境变量值或默认值。
    """
    return os.getenv(name, default)


@dataclass(frozen=True)
class Settings:
    """保存阶段 3 应用的全部运行配置。"""

    llm_provider: str = env("LLM_PROVIDER", "mock")
    embedding_provider: str = env("EMBEDDING_PROVIDER", "hash")
    storage_backend: str = env("STORAGE_BACKEND", "postgres")
    deepseek_api_key: str = env("DEEPSEEK_API_KEY")
    deepseek_base_url: str = env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    deepseek_model: str = env("DEEPSEEK_MODEL", "deepseek-chat")
    siliconflow_api_key: str = env("SILICONFLOW_API_KEY")
    siliconflow_base_url: str = env(
        "SILICONFLOW_BASE_URL",
        "https://api.siliconflow.cn/v1",
    )
    siliconflow_model: str = env(
        "SILICONFLOW_MODEL",
        "Qwen/Qwen2.5-72B-Instruct",
    )
    siliconflow_embedding_model: str = env(
        "SILICONFLOW_EMBEDDING_MODEL",
        "BAAI/bge-m3",
    )
    chroma_host: str = env("CHROMA_HOST", "127.0.0.1")
    chroma_port: int = int(env("CHROMA_PORT", "8001"))
    collection_name: str = env("COLLECTION_NAME", "papermind")
    papers_file: Path = Path(env("PAPERS_FILE", "data/papers.json"))
    postgres_host: str = env("POSTGRES_HOST", "127.0.0.1")
    postgres_port: int = int(env("POSTGRES_PORT", "5432"))
    postgres_db: str = env("POSTGRES_DB", "papermind")
    postgres_user: str = env("POSTGRES_USER", "papermind")
    postgres_password: str = env("POSTGRES_PASSWORD", "papermind")
    redis_url: str = env("REDIS_URL", "redis://localhost:6379/0")
    rag_min_rerank_score: float = float(env("RAG_MIN_RERANK_SCORE", "0.0"))

    @property
    def postgres_url(self) -> str:
        """构建 psycopg 使用的 SQLAlchemy 连接地址。

        :return: 显式 `POSTGRES_URL` 或由分项配置组装的地址。
        """
        direct_url = env("POSTGRES_URL")
        if direct_url:
            return direct_url
        return (
            "postgresql+psycopg://"
            f"{quote_plus(self.postgres_user)}:{quote_plus(self.postgres_password)}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )
```

初次语义基线可暂将 `RAG_MIN_RERANK_SCORE` 设为 `0.0`，但在完成拒答评测前不能发布为默认策略；把最终阈值写入 `.env.example` 和发布记录。

### HTTP 契约与阶段 3 最终装配

用下面内容替换 `app/schemas/search.py`。保留 `insufficient_evidence` 是为了兼容阶段 1 前端，但其值必须由 `answer_status` 推导：

```python
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.application.rag_service import AnswerStatus
from app.domain.knowledge.retrieval import SearchFilters


class SearchRequest(BaseModel):
    """校验专题检索和问答的请求体。"""

    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    index_version: str | None = None
    published_from: datetime | None = None
    categories: list[str] = Field(default_factory=list, max_length=20)
    favorite_only: bool = False

    def to_filters(self) -> SearchFilters:
        """转换为应用层使用的不可变过滤条件。

        :return: 日期、去空白分类和收藏过滤条件。
        """
        return SearchFilters(
            published_from=self.published_from,
            categories=tuple(item.strip() for item in self.categories if item.strip()),
            favorite_only=self.favorite_only,
        )


class SourceResponse(BaseModel):
    """表示可定位且保留各阶段分数的检索来源。"""

    paper_id: str
    title: str
    url: str
    pdf_url: str | None
    chunk_id: UUID
    section_title: str | None
    page_start: int
    page_end: int
    text: str
    semantic_distance: float | None
    keyword_score: float | None
    rerank_score: float


class SearchResponse(BaseModel):
    """表示混合检索响应。"""

    sources: list[SourceResponse]
    index_version: str


class AskResponse(SearchResponse):
    """表示带可解释状态的问答响应。"""

    answer: str
    answer_status: AnswerStatus
    insufficient_evidence: bool
```

用下面内容替换 `app/api/v1/search.py`，确保两条路径使用完全相同的过滤条件：

```python
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies import get_rag_service, get_retrieval_service
from app.schemas.search import AskResponse, SearchRequest, SearchResponse, SourceResponse


router = APIRouter(tags=["search"])


def source(item) -> SourceResponse:
    """将检索候选转换为 HTTP 来源响应。

    :param item: 应用层返回的检索候选。
    :return: 保留位置和三类分数的来源响应。
    """
    return SourceResponse(
        paper_id=item.paper_id,
        title=item.title,
        url=item.url,
        pdf_url=item.pdf_url,
        chunk_id=item.chunk_id,
        section_title=item.section_title,
        page_start=item.page_start,
        page_end=item.page_end,
        text=item.text,
        semantic_distance=item.semantic_distance,
        keyword_score=item.keyword_score,
        rerank_score=item.rerank_score,
    )


@router.post("/topics/{topic_id}/search", response_model=SearchResponse)
async def search(
    topic_id: UUID,
    request: SearchRequest,
    service=Depends(get_retrieval_service),
) -> SearchResponse:
    """执行专题混合检索。

    :param topic_id: 当前专题 ID。
    :param request: 已校验的检索请求。
    :param service: 注入的混合检索服务。
    :return: 来源和实际索引版本。
    """
    result = await service.search(
        topic_id=topic_id,
        question=request.question,
        top_k=request.top_k,
        index_version=request.index_version,
        filters=request.to_filters(),
    )
    return SearchResponse(
        sources=[source(item) for item in result.sources],
        index_version=result.index_version,
    )


@router.post("/topics/{topic_id}/ask", response_model=AskResponse)
async def ask(
    topic_id: UUID,
    request: SearchRequest,
    service=Depends(get_rag_service),
) -> AskResponse:
    """执行专题问答并返回可解释状态。

    :param topic_id: 当前专题 ID。
    :param request: 已校验的问答请求。
    :param service: 注入的 RAG 服务。
    :return: 回答、来源、状态和实际索引版本。
    """
    result = await service.ask(
        topic_id=topic_id,
        question=request.question,
        top_k=request.top_k,
        index_version=request.index_version,
        filters=request.to_filters(),
    )
    return AskResponse(
        answer=result.answer,
        sources=[source(item) for item in result.sources],
        answer_status=result.answer_status,
        insufficient_evidence=result.insufficient_evidence,
        index_version=result.index_version,
    )
```

用下面内容替换阶段 2 的聚合 Router，确保全文路由真正注册：

```python
from fastapi import APIRouter

from app.api.v1 import (
    collection,
    documents,
    index_jobs,
    indexing,
    jobs,
    search,
    subscriptions,
    topic_papers,
    topics,
)


router = APIRouter(prefix="/api/v1")
router.include_router(topics.router)
router.include_router(topic_papers.router)
router.include_router(collection.router)
router.include_router(indexing.router)
router.include_router(subscriptions.router)
router.include_router(index_jobs.router)
router.include_router(jobs.router)
router.include_router(documents.router)
router.include_router(search.router)
```

用下面内容完整替换阶段 2 的 `app/core/container.py`。这个版本保留专题、采集、订阅和任务装配，并加入全文、混合检索和 IndexJob worker；不需要手工合并历史片段：

```python
from uuid import UUID

from app.application.collection_execution_service import CollectionExecutionService
from app.application.collection_job_service import CollectionJobService
from app.application.collection_service import CollectionService
from app.application.document_service import DocumentService
from app.application.hybrid_retrieval_service import HybridRetrievalService
from app.application.index_execution_service import IndexExecutionService
from app.application.index_job_service import IndexJobService
from app.application.indexing_service import IndexingService
from app.application.rag_service import RAGService
from app.application.reranking import ReciprocalRankReranker
from app.application.subscription_scheduler import SubscriptionScheduler
from app.application.subscription_service import SubscriptionService
from app.application.topic_paper_service import TopicPaperService
from app.application.topic_service import TopicService
from app.core.config import Settings
from app.infrastructure.ai.embeddings import create_embedding_client
from app.infrastructure.ai.llm import create_llm_client
from app.infrastructure.arxiv.collector import collect_arxiv
from app.infrastructure.documents.downloader import HttpPdfDownloader
from app.infrastructure.documents.pymupdf_parser import PyMuPdfParser
from app.infrastructure.persistence.collection_job_repository import (
    PostgresCollectionJobRepository,
)
from app.infrastructure.persistence.database import session_factory
from app.infrastructure.persistence.document_repository import (
    PostgresChunkRepository,
    PostgresDocumentRepository,
)
from app.infrastructure.persistence.index_version_repository import (
    PostgresIndexVersionRepository,
)
from app.infrastructure.persistence.keyword_searcher import PostgresKeywordSearcher
from app.infrastructure.persistence.paper_repository import PostgresPaperRepository
from app.infrastructure.persistence.subscription_repository import (
    PostgresIndexJobRepository,
    PostgresSubscriptionRepository,
)
from app.infrastructure.persistence.topic_repository import PostgresTopicRepository
from app.infrastructure.tasks.dispatcher import CeleryTaskDispatcher
from app.infrastructure.vector.chroma import ChromaVectorStore


DEFAULT_WORKSPACE_ID = UUID("00000000-0000-0000-0000-000000000001")


class AppContainer:
    """在应用边界集中创建阶段 3 的生产适配器和服务。"""

    def __init__(self) -> None:
        settings = Settings()
        self.settings = settings
        self.session_factory = session_factory

        self.topic_repository = PostgresTopicRepository(session_factory)
        self.paper_repository = PostgresPaperRepository(session_factory)
        self.document_repository = PostgresDocumentRepository(session_factory)
        self.chunk_repository = PostgresChunkRepository(session_factory)
        self.index_version_repository = PostgresIndexVersionRepository(session_factory)
        self.collection_job_repository = PostgresCollectionJobRepository(session_factory)
        self.subscription_repository = PostgresSubscriptionRepository(session_factory)
        self.index_job_repository = PostgresIndexJobRepository(session_factory)

        self.embeddings = create_embedding_client(settings)
        self.llm = create_llm_client(settings)
        self.vector_store = ChromaVectorStore(
            settings.chroma_host,
            settings.chroma_port,
            settings.collection_name,
        )
        self.keyword_searcher = PostgresKeywordSearcher(session_factory)
        self.reranker = ReciprocalRankReranker()
        self.dispatcher = CeleryTaskDispatcher()

        self.topic_service = TopicService(self.topic_repository, DEFAULT_WORKSPACE_ID)
        self.topic_paper_service = TopicPaperService(
            self.topic_repository,
            self.paper_repository,
        )
        self.collection_service = CollectionService(collect_arxiv, self.paper_repository)
        self.indexing_service = IndexingService(
            self.paper_repository,
            self.embeddings,
            self.vector_store,
        )
        self.retrieval_service = HybridRetrievalService(
            self.embeddings,
            self.vector_store,
            self.index_version_repository,
            self.keyword_searcher,
            self.reranker,
        )
        self.rag_service = RAGService(
            self.retrieval_service,
            self.llm,
            settings.rag_min_rerank_score,
        )

        self.collection_job_service = CollectionJobService(
            self.collection_job_repository,
            self.dispatcher,
        )
        self.collection_execution_service = CollectionExecutionService(
            self.collection_job_repository,
            self.topic_repository,
            self.paper_repository,
            self.subscription_repository,
            collect_arxiv,
        )
        self.subscription_service = SubscriptionService(
            self.topic_repository,
            self.subscription_repository,
        )
        self.index_job_service = IndexJobService(
            self.topic_repository,
            self.index_job_repository,
            self.dispatcher,
            documents=self.document_repository,
        )
        self.subscription_scheduler = SubscriptionScheduler(
            self.subscription_repository,
            self.collection_job_service,
        )

        self.document_service = DocumentService(
            self.document_repository,
            HttpPdfDownloader(),
            PyMuPdfParser(),
            self.topic_repository,
        )
        self.index_execution_service = IndexExecutionService(
            self.document_repository,
            self.document_service,
            self.chunk_repository,
            self.embeddings,
            self.vector_store,
            self.index_version_repository,
            self.index_job_repository,
        )
```

以下断言必须在启动 worker 前通过：

```bash
uv run python -c "from app.core.container import AppContainer; c=AppContainer(); assert c.index_execution_service and c.retrieval_service and c.rag_service"
```

前置条件：PostgreSQL、Chroma 和 Redis 已按检查、创建、验证顺序启动，`0003` 已应用，`.env` 中的连接地址可用；否则不要运行这条真实容器装配检查。

### 自动化验证

创建 `tests/unit/test_reranking.py`，先固定“去重但不丢原始分数”这个不变量：

```python
from dataclasses import replace
from uuid import uuid4

from app.application.reranking import ReciprocalRankReranker
from app.domain.knowledge.retrieval import RetrievalCandidate


def candidate(**values: object) -> RetrievalCandidate:
    """创建包含可定位元数据的测试候选。

    :param values: 要覆盖的候选字段。
    :return: 可用于排序测试的候选。
    """
    defaults = {
        "paper_id": "p1",
        "title": "Paper One",
        "url": "https://example.test/p1",
        "pdf_url": "https://example.test/p1.pdf",
        "chunk_id": uuid4(),
        "section_title": "Methods",
        "page_start": 3,
        "page_end": 4,
        "text": "evidence",
    }
    return RetrievalCandidate(**{**defaults, **values})


def test_rrf_deduplicates_and_preserves_both_scores() -> None:
    """验证两路的同一片段只返回一次。

    :return: None；通过断言验证分数保留与融合顺序。
    """
    semantic = candidate(semantic_distance=0.12)
    keyword = replace(semantic, semantic_distance=None, keyword_score=0.8)

    result = ReciprocalRankReranker().rank([semantic], [keyword], 5)

    assert len(result) == 1
    assert result[0].semantic_distance == 0.12
    assert result[0].keyword_score == 0.8
    assert result[0].rerank_score > 0
```

创建 `tests/unit/test_hybrid_retrieval_service.py`，验证允许的论文 ID 真正传入向量库，而不是只在查询后过滤：

```python
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.application.hybrid_retrieval_service import HybridRetrievalService
from app.application.reranking import ReciprocalRankReranker
from app.domain.knowledge.retrieval import SearchFilters


class FakeEmbeddings:
    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """返回与输入数量一致的固定向量。

        :param texts: 待编码的文本。
        :return: 固定二维测试向量。
        """
        return [[1.0, 0.0] for _ in texts]


class FakeVectorStore:
    def __init__(self, chunk_id: UUID) -> None:
        self.chunk_id = chunk_id
        self.paper_ids: list[str] | None = None

    def query_document_chunks(self, embedding, **options):
        """记录向量过滤并返回一条全文候选。

        :param embedding: 查询向量。
        :param options: 专题、版本、数量和论文过滤。
        :return: Chroma 适配器格式的候选列表。
        """
        assert embedding == [1.0, 0.0]
        self.paper_ids = options["paper_ids"]
        return [{
            "paper_id": "p1",
            "chunk_id": str(self.chunk_id),
            "section_title": "Methods",
            "page_start": 3,
            "page_end": 4,
            "text": "hybrid evidence",
            "score": 0.1,
        }]


class FakeKeywords:
    def allowed_paper_ids(self, topic_id, filters):
        """返回符合专题与元数据过滤的论文。

        :param topic_id: 专题 ID。
        :param filters: 元数据过滤。
        :return: 唯一允许的论文 ID。
        """
        return {"p1"}

    def paper_details(self, paper_ids):
        """返回来源展示元数据。

        :param paper_ids: 待查询论文 ID。
        :return: 标题、原文链接和 PDF 链接。
        """
        assert paper_ids == {"p1"}
        return {"p1": ("Paper One", "https://example.test/p1", None)}

    def search(self, topic_id, query, filters, limit):
        """返回空关键词候选以隔离向量过滤测试。

        :param topic_id: 专题 ID。
        :param query: 检索问题。
        :param filters: 元数据过滤。
        :param limit: 候选上限。
        :return: 空候选列表。
        """
        return []


@pytest.mark.asyncio
async def test_allowed_papers_are_applied_inside_vector_query() -> None:
    """验证向量查询使用与关键词查询相同的论文边界。

    :return: None；通过断言验证向量过滤与来源元数据。
    """
    vector_store = FakeVectorStore(uuid4())
    service = HybridRetrievalService(
        FakeEmbeddings(),
        vector_store,
        SimpleNamespace(get_ready=lambda topic_id: SimpleNamespace(version="v1")),
        FakeKeywords(),
        ReciprocalRankReranker(),
    )

    result = await service.search(
        topic_id=uuid4(),
        question="使用什么研究方法",
        top_k=5,
        filters=SearchFilters(categories=("cs.CL",)),
    )

    assert vector_store.paper_ids == ["p1"]
    assert result.sources[0].title == "Paper One"
    assert result.sources[0].section_title == "Methods"
```

创建 `tests/unit/test_rag_service.py` 的阶段 3 完整替换版本：

```python
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.application.rag_service import AnswerStatus, RAGService
from app.domain.knowledge.retrieval import RetrievalCandidate


def source(score: float) -> RetrievalCandidate:
    """创建具有指定重排分的测试来源。

    :param score: 最终重排分。
    :return: 可定位的测试来源。
    """
    return RetrievalCandidate(
        paper_id="p1",
        title="Paper One",
        url="https://example.test/p1",
        pdf_url=None,
        chunk_id=uuid4(),
        section_title="Methods",
        page_start=3,
        page_end=4,
        text="evidence",
        rerank_score=score,
    )


class FakeRetrieval:
    def __init__(self, evidence: list[RetrievalCandidate]) -> None:
        self.evidence = evidence

    async def search(self, **options):
        """返回预置的检索来源。

        :param options: RAG 服务传入的检索参数。
        :return: 带固定索引版本的检索结果。
        """
        return SimpleNamespace(sources=self.evidence, index_version="v1")


class FailingLLM:
    calls = 0

    async def generate(self, question: str, contexts: list[str]) -> str:
        """记录调用并模拟模型服务故障。

        :param question: 用户问题。
        :param contexts: 检索证据。
        :return: 本 Fake 不会正常返回。
        :raises RuntimeError: 每次调用都抛出。
        """
        self.calls += 1
        raise RuntimeError("provider unavailable")


@pytest.mark.asyncio
async def test_provider_failure_keeps_sources() -> None:
    """验证模型故障不会丢失已检索证据。

    :return: None；通过断言验证降级状态与来源。
    """
    llm = FailingLLM()
    result = await RAGService(FakeRetrieval([source(0.03)]), llm, 0.02).ask(
        topic_id=uuid4(),
        question="question",
        top_k=5,
    )

    assert result.answer_status is AnswerStatus.GENERATION_UNAVAILABLE
    assert len(result.sources) == 1
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_low_score_refuses_without_calling_model() -> None:
    """验证低于阈值时在生成前拒答。

    :return: None；通过断言验证拒答状态与零模型调用。
    """
    llm = FailingLLM()
    result = await RAGService(FakeRetrieval([source(0.01)]), llm, 0.02).ask(
        topic_id=uuid4(),
        question="question",
        top_k=5,
    )

    assert result.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert len(result.sources) == 1
    assert llm.calls == 0
```

创建 `tests/integration/test_keyword_searcher.py`。该测试用一个外层事务回滚数据，且不得用 SQLite 代替，因为 `ARRAY.overlap` 是 PostgreSQL 行为：

```python
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.domain.knowledge.retrieval import SearchFilters
from app.infrastructure.persistence.keyword_searcher import PostgresKeywordSearcher


@pytest.mark.integration
def test_metadata_filters_limit_keyword_candidates() -> None:
    """验证日期、分类和收藏条件同时约束关键词召回。

    :return: None；通过断言验证 PostgreSQL 过滤与中文词项召回。
    """
    engine = create_engine(os.environ["PAPER_MIND_TEST_POSTGRES_URL"])
    connection = engine.connect()
    transaction = connection.begin()
    try:
        workspace_id = uuid4()
        topic_id = uuid4()
        document_id = uuid4()
        chunk_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO workspaces (id, name) VALUES (:id, :name)"
            ),
            {"id": workspace_id, "name": f"workspace-{workspace_id}"},
        )
        connection.execute(
            text(
                """
                INSERT INTO topics
                    (id, workspace_id, name, description, keywords, categories)
                VALUES
                    (:id, :workspace_id, :name, '', ARRAY['研究方法'], ARRAY['cs.CL'])
                """
            ),
            {"id": topic_id, "workspace_id": workspace_id, "name": f"topic-{topic_id}"},
        )
        connection.execute(
            text(
                """
                INSERT INTO papers
                    (paper_id, title, abstract, authors, url, pdf_url,
                     published_at, parse_status, categories)
                VALUES
                    ('p-allowed', 'Allowed', '', '[]'::json,
                     'https://example.test/allowed', NULL,
                     '2026-01-02T00:00:00+00:00', 'parsed', ARRAY['cs.CL']),
                    ('p-excluded', 'Excluded', '', '[]'::json,
                     'https://example.test/excluded', NULL,
                     '2020-01-02T00:00:00+00:00', 'parsed', ARRAY['cs.CV'])
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO topic_papers (topic_id, paper_id, is_favorite)
                VALUES (:topic_id, 'p-allowed', true),
                       (:topic_id, 'p-excluded', false)
                """
            ),
            {"topic_id": topic_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO documents (id, paper_id, source_url, file_path, status)
                VALUES (:id, 'p-allowed', 'https://example.test/allowed.pdf',
                        '/tmp/allowed.pdf', 'parsed')
                """
            ),
            {"id": document_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO document_chunks
                    (id, document_id, paper_id, topic_id, chunk_index, text,
                     section_title, page_start, page_end, chunker_version)
                VALUES
                    (:id, :document_id, 'p-allowed', :topic_id, 0,
                     '该论文使用实验研究方法', 'Methods', 3, 4, 'v1')
                """
            ),
            {"id": chunk_id, "document_id": document_id, "topic_id": topic_id},
        )
        factory = sessionmaker(bind=connection, expire_on_commit=False)
        searcher = PostgresKeywordSearcher(factory)

        result = searcher.search(
            topic_id,
            "使用什么研究方法",
            SearchFilters(
                published_from=datetime(2025, 1, 1, tzinfo=UTC),
                categories=("cs.CL",),
                favorite_only=True,
            ),
            limit=5,
        )

        assert [item.chunk_id for item in result] == [chunk_id]
        assert result[0].title == "Allowed"
        assert result[0].keyword_score is not None
    finally:
        transaction.rollback()
        connection.close()
        engine.dispose()
```

该文件依赖 `0003` 已在专用测试库完成；如果环境变量缺失，应在运行测试前失败，不要把必须的集成检查静默跳过。再以同一固定数据集分别运行向量基线和混合策略，输出 Recall@K、MRR、nDCG、拒答正确率、P95 延迟和模型调用次数。

创建 `evaluation/metrics.py`，让核心排序指标使用同一实现：

```python
import math


def recall_at_k(ranked_ids: list[str], relevant_ids: set[str], k: int) -> float:
    """计算前 K 个结果覆盖的相关项比例。

    :param ranked_ids: 按检索顺序排列的结果 ID。
    :param relevant_ids: 标注为相关的 ID 集合。
    :param k: 评估截断位置。
    :return: Recall@K；没有相关标注时返回 0。
    """
    if not relevant_ids:
        return 0.0
    return len(set(ranked_ids[:k]) & relevant_ids) / len(relevant_ids)


def reciprocal_rank(ranked_ids: list[str], relevant_ids: set[str]) -> float:
    """计算第一个相关结果的倒数排名。

    :param ranked_ids: 按检索顺序排列的结果 ID。
    :param relevant_ids: 标注为相关的 ID 集合。
    :return: 第一个相关结果的倒数排名；未命中时返回 0。
    """
    for rank, item_id in enumerate(ranked_ids, start=1):
        if item_id in relevant_ids:
            return 1 / rank
    return 0.0


def ndcg_at_k(ranked_ids: list[str], relevant_ids: set[str], k: int) -> float:
    """计算二元相关标注下的 nDCG@K。

    :param ranked_ids: 按检索顺序排列的结果 ID。
    :param relevant_ids: 标注为相关的 ID 集合。
    :param k: 评估截断位置。
    :return: 归一化折损累计增益；没有相关标注时返回 0。
    """
    gains = [1.0 if item_id in relevant_ids else 0.0 for item_id in ranked_ids[:k]]
    dcg = sum(gain / math.log2(rank + 1) for rank, gain in enumerate(gains, start=1))
    ideal_length = min(len(relevant_ids), k)
    if ideal_length == 0:
        return 0.0
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, ideal_length + 1))
    return dcg / ideal


def citation_precision(predicted_chunk_ids: list[str], allowed_chunk_ids: set[str]) -> float:
    """计算回答引用中属于允许证据范围的比例。

    :param predicted_chunk_ids: 回答实际引用的片段 ID。
    :param allowed_chunk_ids: 标注或检索阶段允许引用的片段 ID。
    :return: 引用正确率；没有引用时返回 0。
    """
    if not predicted_chunk_ids:
        return 0.0
    return sum(item_id in allowed_chunk_ids for item_id in predicted_chunk_ids) / len(
        predicted_chunk_ids
    )
```

用下面内容完整替换本阶段前文的 `evaluation/scripts/run_retrieval_eval.py`：

```python
import argparse
import json
import math
import time
from pathlib import Path
from typing import Any

import httpx

from evaluation.metrics import (
    citation_precision,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
)


def mean(values: list[float]) -> float | None:
    """计算可能为空的指标列表均值。

    :param values: 单样本指标值。
    :return: 算术平均值；列表为空时返回 None。
    """
    return sum(values) / len(values) if values else None


def percentile_95(values: list[float]) -> float | None:
    """计算小样本也可使用的最近排名 P95。

    :param values: 以毫秒为单位的耗时列表。
    :return: P95 耗时；列表为空时返回 None。
    """
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def post_json(
    client: httpx.Client,
    path: str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], float]:
    """调用 PaperMind API 并记录端到端耗时。

    :param client: 带 API 基础地址的 HTTP 客户端。
    :param path: API 相对路径。
    :param payload: JSON 请求体。
    :return: JSON 响应和毫秒耗时。
    :raises httpx.HTTPStatusError: API 返回非成功状态时抛出。
    """
    started = time.perf_counter()
    response = client.post(path, json=payload)
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.raise_for_status()
    return response.json(), elapsed_ms


def main() -> None:
    """运行固定数据集的检索、引用和拒答评测。

    :return: None；将机器可读报告输出到标准输出和可选文件。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--topic-id", required=True)
    parser.add_argument("--index-version", required=True)
    parser.add_argument("--strategy", default="hybrid-rrf")
    parser.add_argument(
        "--dataset",
        default="evaluation/datasets/disinformation_v1.jsonl",
    )
    parser.add_argument("--output")
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    dataset = [
        json.loads(line)
        for line in dataset_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    metrics: dict[str, list[float]] = {
        "recall_at_5": [],
        "mrr": [],
        "ndcg_at_5": [],
        "citation_precision": [],
        "abstention_accuracy": [],
    }
    latencies_ms: list[float] = []
    failures: list[str] = []
    model_calls = 0

    with httpx.Client(base_url=args.api_base.rstrip("/"), timeout=60.0) as client:
        for item in dataset:
            payload = {
                "question": item["question"],
                "top_k": 5,
                "index_version": args.index_version,
            }
            search, search_ms = post_json(
                client,
                f"/api/v1/topics/{args.topic_id}/search",
                payload,
            )
            answer, answer_ms = post_json(
                client,
                f"/api/v1/topics/{args.topic_id}/ask",
                payload,
            )
            latencies_ms.extend([search_ms, answer_ms])
            ranked_chunk_ids = [str(source["chunk_id"]) for source in search["sources"]]
            answer_chunk_ids = [str(source["chunk_id"]) for source in answer["sources"]]
            relevant = set(item["relevant_chunk_ids"])

            if item["expected_behavior"] == "retrieve":
                metrics["recall_at_5"].append(recall_at_k(ranked_chunk_ids, relevant, 5))
                metrics["mrr"].append(reciprocal_rank(ranked_chunk_ids, relevant))
                metrics["ndcg_at_5"].append(ndcg_at_k(ranked_chunk_ids, relevant, 5))
                metrics["citation_precision"].append(
                    citation_precision(answer_chunk_ids, relevant)
                )
                passed = answer["answer_status"] == "answered" and bool(
                    set(answer_chunk_ids) & relevant
                )
            elif item["expected_behavior"] == "abstain":
                passed = answer["answer_status"] == "insufficient_evidence"
                metrics["abstention_accuracy"].append(float(passed))
            else:
                raise ValueError(
                    f"unknown expected_behavior: {item['expected_behavior']}"
                )

            if answer["answer_status"] in {"answered", "generation_unavailable"}:
                model_calls += 1
            if not passed:
                failures.append(item["query_id"])

    report = {
        "dataset": str(dataset_path),
        "dataset_version": dataset_path.stem,
        "topic_id": args.topic_id,
        "index_version": args.index_version,
        "strategy": args.strategy,
        **{name: mean(values) for name, values in metrics.items()},
        "p95_latency_ms": percentile_95(latencies_ms),
        "model_calls": model_calls,
        "sample_count": len(dataset),
        "failure_query_ids": failures,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
```

脚本对每条检索样本记录 Recall@K、MRR、nDCG 和引用正确性，对拒答样本记录 `answer_status` 是否为 `insufficient_evidence`；同时记录 HTTP 耗时、推定的模型调用次数和失败样本 ID。评测集中的 `relevant_chunk_ids` 必须在构建固定索引后由人工核对并替换成真实 UUID；`chunk-001` 之类示例值不得用于发布门禁。

前置条件：上述领域、Repository、Service、Router、Container 和测试均已保存；单元/API 测试使用 Fake，PostgreSQL 测试使用可丢弃数据库。满足后运行：

```bash
uv run pytest tests/unit/test_reranking.py tests/unit/test_hybrid_retrieval_service.py tests/unit/test_rag_service.py tests/api/test_search.py -q
PAPER_MIND_TEST_POSTGRES_URL=<可丢弃的数据库 URL> uv run pytest tests/integration/test_keyword_searcher.py -m integration -q
```

### 前端：显示全文来源、章节与拒答状态

用下面内容完整替换阶段 1 的 `frontend-web/src/api/topics.ts`。阶段 5 会在此基础上加入阅读状态，阶段 3 不提前声明该字段：

```ts
export type Topic = {
  id: string;
  name: string;
  description: string;
  keywords: string[];
  categories: string[];
};

export type TopicPaper = {
  paper_id: string;
  title?: string;
  authors?: string[];
  url?: string;
  pdf_url?: string;
  is_favorite: boolean;
};

export type Source = {
  paper_id: string;
  title: string;
  url: string;
  pdf_url?: string;
  chunk_id: string;
  section_title?: string;
  page_start: number;
  page_end: number;
  text: string;
  semantic_distance?: number;
  keyword_score?: number;
  rerank_score: number;
};

export type Answer = {
  answer: string;
  answer_status: "answered" | "insufficient_evidence" | "generation_unavailable";
  insufficient_evidence: boolean;
  sources: Source[];
  index_version: string;
};

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as {
      detail?: string;
      title?: string;
    } | null;
    throw new Error(body?.detail ?? body?.title ?? `请求失败（${response.status}）`);
  }
  return response.status === 204
    ? (undefined as T)
    : response.json() as Promise<T>;
}

export function listTopics(): Promise<{ items: Topic[] }> {
  return request("/api/v1/topics");
}

export function createTopic(
  input: Pick<Topic, "name" | "description" | "keywords" | "categories">,
): Promise<Topic> {
  return request("/api/v1/topics", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getTopic(topicId: string): Promise<Topic> {
  return request(`/api/v1/topics/${topicId}`);
}

export function listTopicPapers(topicId: string): Promise<{ items: TopicPaper[] }> {
  return request(`/api/v1/topics/${topicId}/papers`);
}

export function setFavorite(
  topicId: string,
  paperId: string,
  isFavorite: boolean,
): Promise<TopicPaper> {
  return request(`/api/v1/topics/${topicId}/papers/${encodeURIComponent(paperId)}`, {
    method: "PATCH",
    body: JSON.stringify({ is_favorite: isFavorite }),
  });
}

export function searchTopic(
  topicId: string,
  question: string,
): Promise<{ sources: Source[]; index_version: string }> {
  return request(`/api/v1/topics/${topicId}/search`, {
    method: "POST",
    body: JSON.stringify({ question }),
  });
}

export function askTopic(topicId: string, question: string): Promise<Answer> {
  return request(`/api/v1/topics/${topicId}/ask`, {
    method: "POST",
    body: JSON.stringify({ question }),
  });
}
```

用下面内容完整替换 `frontend-web/src/components/SourceList.tsx`：

```tsx
import { Source } from "../api/topics";

export function SourceList({ sources }: { sources: Source[] }) {
  if (sources.length === 0) return <p>没有可定位的来源。</p>;
  return <ol>{sources.map((source) => <li key={source.chunk_id}>
    <a href={source.pdf_url ?? source.url} target="_blank" rel="noreferrer">
      {source.title}
    </a>
    <span>，{source.section_title ?? "未识别章节"}，第 {source.page_start}–{source.page_end} 页</span>
    <blockquote>{source.text}</blockquote>
  </li>)}</ol>;
}
```

用下面内容完整替换 `frontend-web/src/components/SearchPanel.tsx`。检索和问答仍分别发起请求，因此可以分别观察召回来源和实际回答来源；模型不可用时保留来源而不是把它伪装成拒答：

```tsx
import { FormEvent, useState } from "react";

import { Answer, askTopic, searchTopic, Source } from "../api/topics";
import { SourceList } from "./SourceList";


const statusMessages: Record<Answer["answer_status"], string> = {
  answered: "回答已基于下列来源生成。",
  insufficient_evidence: "当前专题的证据不足，系统没有调用模型生成结论。",
  generation_unavailable: "已检索到下列证据，但模型服务暂不可用。",
};


export function SearchPanel({ topicId }: { topicId: string }) {
  const [question, setQuestion] = useState("");
  const [sources, setSources] = useState<Source[]>([]);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      const [search, nextAnswer] = await Promise.all([
        searchTopic(topicId, question),
        askTopic(topicId, question),
      ]);
      setSources(search.sources);
      setAnswer(nextAnswer);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "检索失败");
    } finally {
      setLoading(false);
    }
  }

  return <section>
    <h2>专题内检索与问答</h2>
    <form onSubmit={submit}>
      <input
        value={question}
        onChange={(event) => setQuestion(event.target.value)}
        placeholder="输入研究问题"
        required
      />
      <button disabled={loading} type="submit">
        {loading ? "正在检索…" : "检索并提问"}
      </button>
    </form>
    {error && <p role="alert">{error}</p>}
    {answer && <>
      <h3>回答</h3>
      <p>{answer.answer}</p>
      <p role="status">{statusMessages[answer.answer_status]}</p>
      <p>索引版本：{answer.index_version || "尚无 ready 索引"}</p>
    </>}
    <SourceList sources={sources} />
  </section>;
}
```

## 阶段 3 文件收口清单

- 新增全文链路：`app/domain/documents/`、`app/infrastructure/documents/`、`app/application/document_service.py`、`index_execution_service.py`、`app/infrastructure/persistence/document_models.py`、`document_repository.py`、`index_version_models.py`、`index_version_repository.py`。
- 新增混合检索：`app/domain/knowledge/retrieval.py`、`app/infrastructure/persistence/keyword_searcher.py`、`app/application/reranking.py`、`hybrid_retrieval_service.py`。
- 新增 HTTP、任务和评测：`app/schemas/documents.py`、`search.py`、`app/api/v1/documents.py`、`search.py`、`app/infrastructure/tasks/index_tasks.py`、`evaluation/metrics.py`、`evaluation/scripts/run_retrieval_eval.py` 和固定数据集。
- 新增数据库与测试：`migrations/versions/0003_documents_and_evaluations.py` 及本阶段列出的 unit/API/integration 测试。
- 完整替换：`app/domain/models.py`、`app/infrastructure/persistence/models.py`、`job_models.py`、`app/infrastructure/arxiv/collector.py`、`app/domain/knowledge/chunking.py`、`app/infrastructure/vector/chroma.py`、`app/application/rag_service.py`、`app/core/config.py`、`container.py`、`app/api/v1/router.py`、任务注册与 dispatcher。
- 停止注册：阶段 1 的 `app/api/v1/knowledge.py`；保留文件作学习对照，实际 `/search` 与 `/ask` 只由 `app/api/v1/search.py` 提供。

前置条件：所有文件已保存，`0003` 已应用到可丢弃数据库，PostgreSQL、Redis 和 Chroma 已验证，评测集中的片段 ID 已换成固定索引的真实标注。满足后运行：

```bash
uv run ruff format app tests evaluation
uv run ruff format --check app tests evaluation
uv run ruff check app tests evaluation
POSTGRES_URL=<可丢弃的数据库 URL> uv run alembic upgrade head
uv run pytest tests/unit tests/api -q
PAPER_MIND_TEST_POSTGRES_URL=<可丢弃的数据库 URL> uv run pytest -m integration -q
uv run python evaluation/scripts/run_retrieval_eval.py \
  --topic-id <专题 UUID> \
  --index-version <ready 索引版本>
npm --prefix frontend-web run lint
npm --prefix frontend-web run build
```

全部通过且评测报告达到本阶段门槛后，才进入阶段 4。
