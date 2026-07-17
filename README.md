# PaperMind

面向 arXiv 预印本的研究情报与证据对话应用。

PaperMind 通过研究关键词获取 arXiv 论文，将论文元数据和摘要构建为可检索知识库，并支持基于来源片段的问答。它的目标不是替研究者判断预印本的结论是否正确，而是帮助研究者持续捕捉前沿线索、降低筛选和比较成本，并在可追溯证据基础上形成可进一步验证的研究 Idea。

> arXiv 收录的是预印本，其中的结论尚未必经过同行评审。PaperMind 将其作为研究线索和讨论材料，而非已验证事实；所有回答都应结合返回的论文来源与原文片段人工核对。

## 当前能力

- 按关键词从 arXiv 采集论文标题、摘要、作者、链接和发布时间。
- 支持本地 JSON 或 PostgreSQL 保存论文元数据；当前默认配置可使用 PostgreSQL。
- 将标题和摘要分块，使用 Hash 或 SiliconFlow Embedding 建立 ChromaDB 向量索引。
- 提供语义检索和带来源片段的 RAG 问答。
- 支持 mock、DeepSeek 和 SiliconFlow 的 OpenAI 兼容 LLM Provider。
- 提供 FastAPI 接口与 Gradio 测试界面，方便人工核对检索结果、回答和来源。

## 产品目标

PaperMind 计划演进为一个面向研究者的预印本研究情报工作区：

```text
研究专题订阅
  → 增量采集 arXiv 预印本
  → 筛选、收藏与专题化管理
  → 基于证据的检索和论文比较
  → 形成可追溯的研究假设（Idea）
  → 记录支持证据、冲突证据与待验证问题
```

“Idea Discovery”、专题订阅、阅读笔记和 Idea 卡片目前仍属于后续路线，不应视为已完成能力。

## 架构

```text
                 ┌──────────────── FastAPI ────────────────┐
                 │ /papers/collect  /index/build            │
Gradio 测试界面 ─┤ /search          /ask                    │
                 └─────────────────────────────────────────┘
                                  │
arXiv API → Collector → PaperStorage → Chunker → Embedding → ChromaDB
                         │                         │             │
                         └──── Local JSON / PostgreSQL           │
                                                                   ▼
用户问题 ─────────────────────────────→ Retriever → LLM → Answer + Sources
                                      (DeepSeek / SiliconFlow / mock)
```

## 技术栈

- **后端**：FastAPI、Uvicorn、Pydantic
- **论文采集**：arxiv Python SDK、asyncio
- **存储**：PostgreSQL（SQLAlchemy、psycopg）或本地 JSON
- **向量检索**：ChromaDB Server
- **模型服务**：SiliconFlow Embedding、DeepSeek / SiliconFlow / mock LLM
- **测试界面**：Gradio
- **工程工具**：uv、pytest、Docker Compose

项目不依赖 LangChain 或 LlamaIndex；采集、分块、Embedding、检索和 RAG 编排均由项目自身实现，以便清楚控制每个环节的行为和证据来源。

## 快速开始

### 1. 配置环境变量

```bash
cp .env.example .env
```

默认配置使用本地 JSON、Hash Embedding 和 mock LLM。若要使用当前的真实服务，可在 `.env` 中配置：

```env
STORAGE_BACKEND=postgres
EMBEDDING_PROVIDER=siliconflow
LLM_PROVIDER=deepseek
```

并填写对应的 API Key。不要将 `.env` 提交到版本控制。

### 2. 启动基础设施和 API

```bash
uv sync
docker compose up -d
uv run uvicorn app.main:app --reload
```

FastAPI 文档：<http://127.0.0.1:8000/docs>

### 3. 启动测试界面

在另一个终端执行：

```bash
uv run python -m frontend.app
```

打开 <http://127.0.0.1:7860>，可检查服务状态、采集论文、构建索引、查看检索片段和核对问答来源。

## API 使用顺序

以下是当前 `v0.1.0` 的兼容接口。研究专题、采集任务和会话等资源落地后，将按学习路线迁移到 `/api/v1` 的 RESTful API；不把现有同步动作接口误写为最终 API 形态。

```text
POST /papers/collect  # 按关键词采集 arXiv 论文
POST /index/build     # 对当前论文库构建或更新向量索引
POST /search          # 检索相关论文片段
POST /ask             # 基于检索片段生成回答和来源
```

接口参数和响应模型可直接在 `/docs` 查看。

## 项目结构

```text
PaperMind/
├── app/
│   ├── core/              # 配置与依赖装配
│   ├── api/               # HTTP 路由与依赖注入
│   ├── application/       # 采集、索引、检索、问答用例编排
│   ├── domain/            # 论文、文本块与基础设施抽象端口
│   ├── schemas/           # HTTP 请求/响应模型
│   ├── infrastructure/    # arXiv、存储、Chroma、Embedding、LLM 适配器
│   └── main.py            # FastAPI 应用创建与路由注册
├── frontend/
│   └── app.py             # Gradio 测试界面
├── tests/                 # 单元测试
├── docker-compose.yml     # PostgreSQL 与 ChromaDB
└── pyproject.toml         # Python 依赖与测试配置
```

## 开发与验证

```bash
uv run pytest -q
```

当前测试覆盖健康检查、路由依赖注入、采集转换、本地存储、文本分块、Embedding、检索协调、mock RAG 以及 Gradio 结果格式化。

## 路线图

- [ ] 研究专题订阅与 arXiv 增量采集。
- [ ] 论文库、收藏、标签、阅读状态和任务进度。
- [ ] PDF 全文解析、章节级分块和页码级来源。
- [ ] 检索评测集、重排序、来源去重与拒答阈值。
- [ ] 论文比较、阅读笔记与可追溯 Idea 卡片。
- [ ] 面向日常使用的正式 Web 界面、部署、日志与 CI。
