from app.domain.models import Paper
from app.domain.ports import PaperCollector, PaperRepository


class PaperCollectionService:
    """采集论文并写入仓储的应用服务。"""

    def __init__(self, collector: PaperCollector, repository: PaperRepository) -> None:
        self.collector = collector
        self.repository = repository

    async def collect_and_save(self, query: str, max_results: int) -> list[Paper]:
        papers = await self.collector(query, max_results)
        self.repository.save_many(papers)
        return papers
