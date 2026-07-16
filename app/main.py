from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.chunker import build_chunks
from app.collector import collect_arxiv
from app.config import Settings
from app.embeddings import create_embedding_client
from app.llm import create_llm_client
from app.rag import RAGService
from app.retriever import Retriever
from app.schemas import (
    AskRequest,
    AskResponse,
    CollectRequest,
    CollectResponse,
    IndexResponse,
    SearchRequest,
    SearchResponse,
    )
from app.storage import create_storage
from app.vector_store import ChromaVectorStore


# 在应用启动时装配存储、Embedding、向量库与检索器依赖
settings = Settings()
storage = create_storage(settings)
embedding_client = create_embedding_client(settings)
vector_store = ChromaVectorStore(settings.chroma_host, settings.chroma_port, settings.collection_name)
retriever = Retriever(vector_store, embedding_client)
llm_client = create_llm_client(settings)
rag_service = RAGService(retriever, llm_client)


app = FastAPI(title="PaperMind", version="0.1.0")


@app.get("/health")
async def health_check():
    """返回服务存活状态，供测试、部署探针和人工检查使用。"""
    return {"status": "ok"}


@app.post("/papers/collect", response_model=CollectResponse)
async def collect_papers(request: CollectRequest) -> CollectResponse:
    """采集 arXiv 论文并保存；上游请求失败时转换为 HTTP 502"""
    try:
        papers = await collect_arxiv(request.query, request.max_results)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"paper collection failed: {exc}") from exc
    storage.save_many(papers)
    return CollectResponse(count=len(papers), papers=papers)


@app.post("/index/build", response_model=IndexResponse)
async def build_index() -> IndexResponse:
    """读取论文并创建 ChromaDB 向量索引

    :return: 被处理的论文数和写入的文本块数
    """
    papers = storage.load_all()
    chunks = build_chunks(papers)
    embeddings = await embedding_client.embed_texts([chunk.text for chunk in chunks])
    vector_store .upsert_chunks(chunks, embeddings)
    return IndexResponse(papers=len(papers), chunks=len(chunks))


@app.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest) -> SearchResponse:
    """执行向量检索

    :return: 最多 ``top_k``条相关文本块
    """
    results = await retriever.retrieve(request.query, request.top_k)
    return SearchResponse(results=results)


@app.post("/ask", response_model=AskResponse)
async def ask(request: AskRequest) -> AskResponse:
    """接收用户问题，并通过 RAG 服务返回回答与引用来源

    :return: RAG 服务生成的回答和来源列表
    """
    return await rag_service.ask(request.question, request.top_k)

class Item(BaseModel):
    name: str
    price: float

@app.get("/")
async def read_root():
    return {"message": "Hello PaperMind"}

@app.post("/items")
async def create_item(item: Item):
    return {"message": f"created {item.name}", "price": item.price}
