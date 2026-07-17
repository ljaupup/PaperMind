from fastapi import APIRouter, Depends

from app.api.dependencies import get_indexing_service, get_retrieval_service
from app.application.indexing_service import IndexingService
from app.application.retrieval_service import RetrievalService
from app.schemas.knowledge import IndexResponse, SearchRequest, SearchResponse


router = APIRouter(tags=["knowledge"])


@router.post("/index/build", response_model=IndexResponse)
async def build_index(
    service: IndexingService = Depends(get_indexing_service),
) -> IndexResponse:
    """v0.1 兼容接口：为当前论文库构建向量索引。"""
    papers, chunks = await service.build()
    return IndexResponse(papers=papers, chunks=chunks)


@router.post("/search", response_model=SearchResponse)
async def search(
    request: SearchRequest,
    service: RetrievalService = Depends(get_retrieval_service),
) -> SearchResponse:
    """v0.1 兼容接口：执行向量检索。"""
    return SearchResponse(results=await service.retrieve(request.query, request.top_k))
