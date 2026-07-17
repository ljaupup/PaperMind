from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.infrastructure.persistence.models import Base


def init_db(database_url: str) -> Engine:
    """创建引擎，并创建尚不存在的表；不会修改已有表的结构。"""
    engine = create_engine(database_url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    return engine
