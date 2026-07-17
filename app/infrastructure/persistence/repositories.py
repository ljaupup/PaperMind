import json
from pathlib import Path

from app.core.config import Settings
from app.domain.models import Paper
from app.domain.ports import PaperRepository
from app.infrastructure.persistence.postgres_repository import PostgresPaperRepository


class LocalPaperRepository:
    """以 JSON 文件实现论文仓储，适合本地开发和调试。"""

    def __init__(self, path: Path) -> None:
        """初始化数据文件路径，并确保其父目录存在。"""
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> list[Paper]:
        """从 JSON 文件恢复 Paper 列表；文件不存在时返回空列表。"""
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return [Paper(**item) for item in data]

    def save_many(self, papers: list[Paper]) -> list[Paper]:
        """以 paper_id 去重合并后，将完整列表写回 JSON 文件。"""
        existing = {paper.paper_id: paper for paper in self.load_all()}
        for paper in papers:
            existing[paper.paper_id] = paper
        all_papers = list(existing.values())
        payload = [paper.model_dump() for paper in all_papers]
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return all_papers


def create_paper_repository(settings: Settings) -> PaperRepository:
    """根据配置选择本地 JSON 或 PostgreSQL 论文仓储实现。"""
    if settings.storage_backend == "postgres":
        return PostgresPaperRepository(settings.postgres_url)
    return LocalPaperRepository(settings.papers_file)
