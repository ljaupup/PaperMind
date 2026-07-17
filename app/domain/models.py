from pydantic import BaseModel, Field


class Paper(BaseModel):
    """一篇论文的业务数据。"""

    paper_id: str
    title: str
    abstract: str
    authors: list[str] = Field(default_factory=list)
    url: str
    pdf_url: str | None = None
    file_path: str | None = None
    file_hash: str | None = None
    parse_status: str = "metadata_only"
    published: str = ""


class PaperChunk(BaseModel):
    """一段可检索的论文文本及其来源元数据。"""

    chunk_id: str
    paper_id: str
    title: str
    url: str
    pdf_url: str | None = None
    page: int | None = None
    text: str


class SearchResult(BaseModel):
    """一次检索命中的文本块。"""

    text: str
    title: str
    url: str
    pdf_url: str | None = None
    page: int | None = None
    score: float | None = None


class Source(BaseModel):
    """回答中可追溯的一条证据来源。"""

    title: str
    url: str
    pdf_url: str | None = None
    page: int | None = None
    text: str
    score: float | None = None


class Answer(BaseModel):
    """RAG 用例的领域输出。"""

    answer: str
    sources: list[Source]
