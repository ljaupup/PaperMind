from fastapi import APIRouter, Depends

from app.api.dependencies import get_rag_service
from app.application.rag_service import RAGService
from app.schemas.conversations import AskRequest, AskResponse


router = APIRouter(tags=["conversations"])


@router.post("/ask", response_model=AskResponse)
async def ask(
    request: AskRequest,
    service: RAGService = Depends(get_rag_service),
) -> AskResponse:
    """v0.1 兼容接口：检索论文片段并生成带来源的回答。"""
    return AskResponse.from_answer(await service.ask(request.question, request.top_k))
