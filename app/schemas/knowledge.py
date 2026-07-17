from pydantic import BaseModel, Field

from app.domain.models import SearchResult


class IndexResponse(BaseModel):
    """索引构建接口的响应模型。"""

    papers: int
    chunks: int


class SearchRequest(BaseModel):
    """检索接口的请求模型。"""

    query: str = Field(min_length=1)
    top_k: int = Field(default=3, ge=1, le=10)


class SearchResponse(BaseModel):
    """检索接口的响应模型。"""

    results: list[SearchResult]
