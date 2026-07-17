from functools import cached_property

from app.application.indexing_service import IndexingService
from app.application.paper_service import PaperCollectionService
from app.application.rag_service import RAGService
from app.application.retrieval_service import RetrievalService
from app.core.config import Settings
from app.infrastructure.ai.embeddings import create_embedding_client
from app.infrastructure.ai.llm import create_llm_client
from app.infrastructure.arxiv.collector import collect_arxiv
from app.infrastructure.persistence.repositories import create_paper_repository
from app.infrastructure.vector.chroma import ChromaVectorStore


class AppContainer:
    """按需创建基础设施实现，并装配当前 MVP 的应用服务。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    @cached_property
    def paper_repository(self):
        return create_paper_repository(self.settings)

    @cached_property
    def embedding_client(self):
        return create_embedding_client(self.settings)

    @cached_property
    def vector_store(self) -> ChromaVectorStore:
        return ChromaVectorStore(
            self.settings.chroma_host,
            self.settings.chroma_port,
            self.settings.collection_name,
        )

    @cached_property
    def llm_client(self):
        return create_llm_client(self.settings)

    @cached_property
    def paper_collection_service(self) -> PaperCollectionService:
        return PaperCollectionService(collect_arxiv, self.paper_repository)

    @cached_property
    def retrieval_service(self) -> RetrievalService:
        return RetrievalService(self.vector_store, self.embedding_client)

    @cached_property
    def indexing_service(self) -> IndexingService:
        return IndexingService(
            self.paper_repository,
            self.embedding_client,
            self.vector_store,
        )

    @cached_property
    def rag_service(self) -> RAGService:
        return RAGService(self.retrieval_service, self.llm_client)
