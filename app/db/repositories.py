from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.database import init_db
from app.db.models import PaperRecord
from app.schemas import Paper


class PostgresPaperStorage:
    """以 SQLAlchemy ORM 实现 PostgreSQL 论文存储。"""

    def __init__(self, database_url: str) -> None:
        """创建引擎，并初始化本地开发所需的 ORM 表。"""
        self.engine = init_db(database_url)

    def load_all(self) -> list[Paper]:
        """通过 ORM 查询 papers 表，并转换回项目的 Paper 模型。"""
        with Session(self.engine) as session:
            records = session.scalars(
                select(PaperRecord).order_by(PaperRecord.published.desc())
            ).all()
        return [
            Paper(
                paper_id=record.paper_id,
                title=record.title,
                abstract=record.abstract,
                authors=list(record.authors or []),
                url=record.url,
                pdf_url=record.pdf_url,
                file_path=record.file_path,
                file_hash=record.file_hash,
                parse_status=record.parse_status,
                published=record.published,
            )
            for record in records
        ]

    def save_many(self, papers: list[Paper]) -> list[Paper]:
        """按 paper_id 执行 ORM upsert，避免重复采集产生重复记录。"""
        with Session(self.engine) as session:
            for paper in papers:
                payload = paper.model_dump()
                statement = insert(PaperRecord).values(**payload)
                statement = statement.on_conflict_do_update(
                    index_elements=[PaperRecord.paper_id],
                    set_={
                        key: value
                        for key, value in payload.items()
                        if key != "paper_id"
                    } | {"updated_at": func.now()},
                )
                session.execute(statement)
            session.commit()
        return self.load_all()