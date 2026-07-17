from app.domain.models import Paper
from app.infrastructure.persistence.repositories import LocalPaperRepository


def test_storage_save_and_load(tmp_path) -> None:
    """本地存储应能将 Paper 写入 JSON 后无损读回。"""
    storage = LocalPaperRepository(tmp_path / "papers.json")
    paper = Paper(
        paper_id="p1",
        title="Title",
        abstract="Abstract",
        authors=["Alice"],
        url="https://example.com/p1",
        pdf_url="https://example.com/p1.pdf",
        parse_status="metadata_only",
        published="2024",
    )

    storage.save_many([paper])
    loaded = storage.load_all()

    assert len(loaded) == 1
    assert loaded[0].paper_id == "p1"
    assert loaded[0].pdf_url == "https://example.com/p1.pdf"
    assert loaded[0].parse_status == "metadata_only"
