from typing import Protocol

from app.domain.models import Paper, PaperChunk, SearchResult


class PaperRepository(Protocol):
    """论文持久化端口。"""

    def load_all(self) -> list[Paper]: ...

    def save_many(self, papers: list[Paper]) -> list[Paper]: ...


class PaperCollector(Protocol):
    """外部论文来源采集端口。"""

    async def __call__(self, query: str, max_results: int) -> list[Paper]: ...


class EmbeddingClient(Protocol):
    """文本向量化端口。"""

    async def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


class LLMClient(Protocol):
    """语言模型生成端口。"""

    async def generate(self, question: str, contexts: list[str]) -> str: ...


class VectorStore(Protocol):
    """向量索引读写端口。"""

    def upsert_chunks(self, chunks: list[PaperChunk], embeddings: list[list[float]]) -> None: ...

    def query(self, embedding: list[float], top_k: int) -> list[SearchResult]: ...
