import json
from pathlib import Path
from typing import Protocol

from app.config import Settings
from app.db.repositories import PostgresPaperStorage
from app.schemas import Paper


class PaperStorage(Protocol):
    """存储层契约：调用方只依赖读写论文的能力，不依赖具体介质。"""

    def load_all(self) -> list[Paper]:
        """读取当前后端中的全部论文。"""
        ...

    def save_many(self, papers: list[Paper]) -> list[Paper]:
        """按 paper_id 保存或更新论文，并返回保存后的全部论文。"""
        ...


class LocalPaperStorage:
    """以 JSON 文件实现 PaperStorage，适合本地开发和调试。"""

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


def create_storage(settings: Settings) -> PaperStorage:
    """根据配置选择本地 JSON 或 PostgreSQL 存储实现。"""
    if settings.storage_backend == "postgres":
        return PostgresPaperStorage(settings.postgres_url)
    return LocalPaperStorage(settings.papers_file)