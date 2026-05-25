# PaperMind

AI 论文知识库 — 输入研究关键词，自动爬取论文，建立向量知识库，用自然语言提问你的论文库。一个覆盖 RAG 全流程的学习项目。

## 架构

```
采集模块 (httpx)  →  处理+索引 (ChromaDB)  →  问答接口 (FastAPI)  →  前端展示 (Gradio)
```

## 技术栈

- **后端框架**: FastAPI + Uvicorn
- **向量数据库**: ChromaDB
- **前端**: Gradio
- **HTTP 客户端**: httpx（论文采集）
- **Python**: >= 3.11
- **包管理**: uv

项目不使用 LangChain / LlamaIndex 等全家桶框架，每个组件自己搭建，以深入理解 RAG 每一环。

## 快速开始

```bash
# 安装依赖
uv sync

# 启动 API 服务
uv run uvicorn app.main:app --reload

# 访问 API 文档
# http://localhost:8000/docs
```

## 项目结构

```
PaperMind/
├── app/            # FastAPI 应用
│   └── main.py     # API 入口（/ 和 /health 端点）
├── frontend/       # Gradio 前端
├── tests/          # 测试
├── docs/           # 文档
├── main.py         # CLI 入口
└── pyproject.toml  # 项目配置
```

## 开发

```bash
# 运行测试
uv run pytest

# 类型检查
uv run mypy app/
```
