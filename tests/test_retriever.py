import pytest

from app.application.retrieval_service import RetrievalService
from app.domain.models import SearchResult


class FakeEmbeddingClient:
    """为 Retriever 测试提供固定的查询向量。"""

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """返回与输入数量一致的固定二维向量。

        :return: 每条输入对应的固定向量。
        """
        return [[0.1, 0.2] for _ in texts]


class FakeVectorStore:
    """为 Retriever 测试记录查询参数并返回固定结果。"""

    def __init__(self) -> None:
        """初始化测试断言所需的查询记录。"""
        self.last_embedding: list[float] | None = None
        self.last_top_k: int | None = None

    def query(self, embedding: list[float], top_k: int) -> list[SearchResult]:
        """记录调用参数，并返回一条可预测的检索结果。

        :return: 用于测试断言的固定结果。
        """
        self.last_embedding = embedding
        self.last_top_k = top_k
        return [
            SearchResult(
                text="retrieval result",
                title="Paper",
                url="https://example.com/paper",
                score=0.1,
            )
        ]


@pytest.mark.asyncio
async def test_retriever_embeds_query_and_queries_vector_store() -> None:
    """验证 Retriever 先生成向量，再调用向量库。

    :return: None；通过断言验证预期行为。
    """
    vector_store = FakeVectorStore()
    retriever = RetrievalService(vector_store, FakeEmbeddingClient())

    results = await retriever.retrieve("what is RAG", top_k=2)

    assert vector_store.last_embedding == [0.1, 0.2]
    assert vector_store.last_top_k == 2
    assert results[0].title == "Paper"
