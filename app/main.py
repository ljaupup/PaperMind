from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.collector import collect_arxiv
from app.config import Settings
from app.schemas import CollectRequest, CollectResponse
from app.storage import create_storage


settings = Settings()
storage = create_storage(settings)


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


class Item(BaseModel):
    name: str
    price: float

@app.get("/")
async def read_root():
    return {"message": "Hello PaperMind"}

@app.post("/items")
async def create_item(item: Item):
    return {"message": f"created {item.name}", "price": item.price}
