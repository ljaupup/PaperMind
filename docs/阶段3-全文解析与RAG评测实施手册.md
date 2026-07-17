# 阶段 3：全文解析与 RAG 评测实施手册

> **目标版本：** `v0.4.0`
>
> **阶段目标：** 以论文 PDF 全文替代“只检索标题和摘要”的知识库，并建立可重复、可量化的检索与回答质量评测闭环。
>
> **完成后用户能做什么：** 在专题内得到带论文、章节和页码来源的回答；系统能用指标解释检索策略为何改进，而不是凭主观感觉换模型。

阶段 3 是 PaperMind RAG 从 Demo 走向可信应用的关键阶段。它的优先级高于论文比较、Idea 卡片等生成型功能：没有可靠证据层，后续功能只会放大错误。

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
tests/test_document_service.py
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


class ParsedBlock(BaseModel):
    """解析器交给领域分块器的统一输入。"""

    text: str
    page_number: int
    block_order: int
    section_title: str | None = None


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

from app.domain.documents.models import ParsedBlock


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
app/infrastructure/documents/file_store.py
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

    for block in blocks:
        candidate = "\n".join([*buffer, block.text])
        if buffer and len(candidate) > max_chars:
            flush()
            tail = chunks[-1].text[-overlap_chars:] if overlap_chars else ""
            buffer = [tail] if tail else []
            buffer_pages = [chunks[-1].page_end] if tail else []
        buffer.append(block.text)
        buffer_pages.append(block.page_number)
    flush()
    return chunks
```

当一个候选块超过 `max_chars` 时，示例仅将上一个块末尾的 `overlap_chars` 个字符带入下一个块，避免把整段内容重复写入索引。

为此函数添加测试，至少断言：

```python
assert chunks[0].page_start == 1
assert chunks[-1].page_end == 2
assert all(chunk.chunker_version == "structured-v1" for chunk in chunks)
assert all(chunk.text for chunk in chunks)
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
{"query_id":"d-001","question":"哪些论文讨论了生成式虚假信息的传播机制？","relevant_paper_ids":["2401.00001"],"relevant_chunk_ids":["chunk-001"],"notes":"人工阅读摘要和全文后标注"}
{"query_id":"d-002","question":"当前论文库是否有量化跨平台治理效果的证据？","relevant_paper_ids":[],"relevant_chunk_ids":[],"notes":"应触发拒答或证据不足"}
```

创建 `evaluation/scripts/run_retrieval_eval.py`：

```python
import argparse
import json
from pathlib import Path

import httpx


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 1.0 if not retrieved[:k] else 0.0
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--topic-id", required=True)
    parser.add_argument("--index-version", required=True)
    parser.add_argument("--dataset", default="evaluation/datasets/disinformation_v1.jsonl")
    args = parser.parse_args()

    dataset = [json.loads(line) for line in Path(args.dataset).read_text(encoding="utf-8").splitlines() if line]
    scores: list[float] = []
    for item in dataset:
        results = search(args.api_base, args.topic_id, item["question"], args.index_version, top_k=5)
        ids = [result["paper_id"] for result in results]
        scores.append(recall_at_k(ids, set(item["relevant_paper_ids"]), 5))
    report = {
        "dataset": args.dataset,
        "topic_id": args.topic_id,
        "index_version": args.index_version,
        "recall_at_5": sum(scores) / len(scores),
        "queries": len(scores),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
```

先在 Router 中实现 `POST /api/v1/topics/{topic_id}/search`，请求体包含 `question`、`top_k` 和 `index_version`，响应中返回 `sources` 数组；每项至少有 `paper_id` 与 `chunk_id`。前置条件：Router 已注册，目标专题已有完成的 IndexJob、指定索引版本和标注数据集，API 已启动且可访问。满足后执行评测：

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
