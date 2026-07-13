from pydantic import BaseModel, Field


class Paper(BaseModel):
    """一篇论文的结构化元数据，也是存储层使用的统一对象。"""

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
    """可写入向量数据库并参与检索的一段论文文本。"""

    chunk_id: str
    paper_id: str
    title: str
    url: str
    pdf_url: str | None = None
    page: int | None = None
    text: str


class CollectRequest(BaseModel):
    """POST /papers/collect 接口接收的采集参数。"""

    query: str = Field(min_length=1)
    max_results: int = Field(default=5, ge=1, le=30)


class CollectResponse(BaseModel):
    """论文采集接口返回的论文列表及数量。"""

    count: int
    papers: list[Paper]


class IndexResponse(BaseModel):
    """建立向量索引后返回的论文数与文本块数。"""

    papers: int
    chunks: int


class SearchRequest(BaseModel):
    """POST /search 接口接收的检索参数。"""

    query: str = Field(min_length=1)
    top_k: int = Field(default=3, ge=1, le=10)


class SearchResult(BaseModel):
    """一次向量检索命中的文本块及其来源信息。"""

    text: str
    title: str
    url: str
    pdf_url: str | None = None
    page: int | None = None
    score: float | None = None


class SearchResponse(BaseModel):
    """检索接口返回的结果列表。"""

    results: list[SearchResult]


class AskRequest(BaseModel):
    """POST /ask 接口接收的问题和检索条数。"""

    question: str = Field(min_length=1)
    top_k: int = Field(default=3, ge=1, le=10)


class Source(BaseModel):
    """RAG 回答中可追溯的一条引用来源。"""

    title: str
    url: str
    pdf_url: str | None = None
    page: int | None = None
    text: str
    score: float | None = None


class AskResponse(BaseModel):
    """RAG 接口返回的回答正文及其引用来源。"""

    answer: str
    sources: list[Source]