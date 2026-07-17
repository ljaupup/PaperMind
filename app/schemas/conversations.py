from pydantic import BaseModel, Field

from app.domain.models import Answer, Source


class AskRequest(BaseModel):
    """问答接口的请求模型。"""

    question: str = Field(min_length=1)
    top_k: int = Field(default=3, ge=1, le=10)


class AskResponse(BaseModel):
    """问答接口的响应模型。"""

    answer: str
    sources: list[Source]

    @classmethod
    def from_answer(cls, answer: Answer) -> "AskResponse":
        return cls(answer=answer.answer, sources=answer.sources)
