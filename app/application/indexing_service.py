from app.domain.knowledge.chunking import build_chunks
from app.domain.ports import EmbeddingClient, PaperRepository, VectorStore


class IndexingService:
    """读取论文、分块、向量化并写入检索索引。"""

    def __init__(
        self,
        repository: PaperRepository,
        embedding_client: EmbeddingClient,
        vector_store: VectorStore,
    ) -> None:
        self.repository = repository
        self.embedding_client = embedding_client
        self.vector_store = vector_store

    async def build(self) -> tuple[int, int]:
        papers = self.repository.load_all()
        chunks = build_chunks(papers)
        embeddings = await self.embedding_client.embed_texts([chunk.text for chunk in chunks])
        self.vector_store.upsert_chunks(chunks, embeddings)
        return len(papers), len(chunks)
