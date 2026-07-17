from pydantic import BaseModel, Field

from app.domain.models import Paper


class CollectRequest(BaseModel):
    """论文采集接口的请求模型。"""

    query: str = Field(min_length=1)
    max_results: int = Field(default=5, ge=1, le=30)


class CollectResponse(BaseModel):
    """论文采集接口的响应模型。"""

    count: int
    papers: list[Paper]
