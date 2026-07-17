from app.domain.knowledge.chunking import build_chunks, chunk_text
from app.domain.models import Paper


def test_chunk_text_with_overlap() -> None:
    """固定长度分块应限制长度，并按预期产生重叠块。"""
    text = "a" * 1000
    chunks = chunk_text(text, chunk_size=300, overlap=50)
    assert len(chunks) == 4
    assert all(len(chunk) <= 300 for chunk in chunks)


def test_build_chunks_keeps_source() -> None:
    """分块后的结果必须保留论文来源信息，供检索结果引用。"""
    paper = Paper(
        paper_id="p1",
        title="RAG Paper",
        abstract="retrieval augmented generation " * 50,
        authors=[],
        url="https://example.com/p1",
        pdf_url="https://example.com/p1.pdf",
    )
    chunks = build_chunks([paper])
    assert chunks
    assert chunks[0].paper_id == "p1"
    assert chunks[0].title == "RAG Paper"
    assert chunks[0].url == "https://example.com/p1"
    assert chunks[0].pdf_url == "https://example.com/p1.pdf"
    assert chunks[0].page is None
