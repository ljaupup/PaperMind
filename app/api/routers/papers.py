from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_paper_collection_service
from app.application.paper_service import PaperCollectionService
from app.schemas.papers import CollectRequest, CollectResponse


router = APIRouter(prefix="/papers", tags=["papers"])


@router.post("/collect", response_model=CollectResponse)
async def collect_papers(
    request: CollectRequest,
    service: PaperCollectionService = Depends(get_paper_collection_service),
) -> CollectResponse:
    """v0.1 兼容接口：采集 arXiv 论文并写入当前论文仓储。"""
    try:
        papers = await service.collect_and_save(request.query, request.max_results)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"paper collection failed: {exc}") from exc
    return CollectResponse(count=len(papers), papers=papers)
