import pytest

from app.application.rag_service import RAGService
from app.domain.models import SearchResult
from app.infrastructure.ai.llm import MockLLMClient


class FakeRetriever:
    """为 RAG 单元测试提供固定检索结果，避免依赖 ChromaDB。"""

    async def retrieve(self, query: str, top_k: int = 3) -> list[SearchResult]:
        """始终返回一条可预测的 RAG 论文片段。

        :return: 用于 RAG 测试的固定检索结果。
        """
        return [
            SearchResult(
                text="RAG combines retrieval and generation.",
                title="RAG Paper",
                url="https://example.com/rag",
                score=0.1,
            )
        ]


@pytest.mark.asyncio
async def test_rag_mock_returns_answer_and_sources() -> None:
    """验证 Mock RAG 流程返回回答和可追溯来源。

    :return: None；通过断言验证预期行为。
    """
    service = RAGService(FakeRetriever(), MockLLMClient())
    response = await service.ask("What is RAG?", top_k=1)

    assert "mock" in response.answer
    assert len(response.sources) == 1
    assert response.sources[0].title == "RAG Paper"


@pytest.mark.asyncio
async def test_rag_returns_refusal_when_retrieval_is_empty() -> None:
    class EmptyRetriever:
        async def retrieve(self, query: str, top_k: int = 3) -> list[SearchResult]:
            return []

    response = await RAGService(EmptyRetriever(), MockLLMClient()).ask("unknown")

    assert response.answer == "当前论文库中没有足够信息。"
    assert response.sources == []
