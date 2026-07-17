from fastapi.testclient import TestClient

from app.domain.models import Answer, Paper, SearchResult, Source
from app.main import create_app


class FakePaperCollectionService:
    async def collect_and_save(self, query: str, max_results: int) -> list[Paper]:
        return [
            Paper(
                paper_id="p1",
                title="Collected paper",
                abstract="abstract",
                url="https://example.com/p1",
            )
        ]


class FakeIndexingService:
    async def build(self) -> tuple[int, int]:
        return 1, 2


class FakeRetrievalService:
    async def retrieve(self, query: str, top_k: int) -> list[SearchResult]:
        return [
            SearchResult(
                text="retrieved text",
                title="Retrieved paper",
                url="https://example.com/p1",
                score=0.1,
            )
        ]


class FakeRAGService:
    async def ask(self, question: str, top_k: int) -> Answer:
        return Answer(
            answer="grounded answer",
            sources=[
                Source(
                    title="Retrieved paper",
                    url="https://example.com/p1",
                    text="retrieved text",
                    score=0.1,
                )
            ],
        )


class FakeContainer:
    paper_collection_service = FakePaperCollectionService()
    indexing_service = FakeIndexingService()
    retrieval_service = FakeRetrievalService()
    rag_service = FakeRAGService()


def test_routes_delegate_to_injected_application_services() -> None:
    """路由应通过容器调用应用服务，而不直接创建外部基础设施。"""
    client = TestClient(create_app(FakeContainer()))

    collect = client.post("/papers/collect", json={"query": "rag", "max_results": 1})
    index = client.post("/index/build")
    search = client.post("/search", json={"query": "rag", "top_k": 1})
    ask = client.post("/ask", json={"question": "what is rag?", "top_k": 1})

    assert collect.status_code == 200
    assert collect.json()["papers"][0]["paper_id"] == "p1"
    assert index.json() == {"papers": 1, "chunks": 2}
    assert search.json()["results"][0]["title"] == "Retrieved paper"
    assert ask.json()["answer"] == "grounded answer"
