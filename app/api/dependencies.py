from typing import cast

from fastapi import Depends, Request

from app.application.indexing_service import IndexingService
from app.application.paper_service import PaperCollectionService
from app.application.rag_service import RAGService
from app.application.retrieval_service import RetrievalService
from app.core.container import AppContainer


def get_container(request: Request) -> AppContainer:
    """从 FastAPI 应用状态中获取已装配的依赖容器。"""
    return cast(AppContainer, request.app.state.container)


def get_paper_collection_service(
    container: AppContainer = Depends(get_container),
) -> PaperCollectionService:
    return container.paper_collection_service


def get_indexing_service(
    container: AppContainer = Depends(get_container),
) -> IndexingService:
    return container.indexing_service


def get_retrieval_service(
    container: AppContainer = Depends(get_container),
) -> RetrievalService:
    return container.retrieval_service


def get_rag_service(
    container: AppContainer = Depends(get_container),
) -> RAGService:
    return container.rag_service
