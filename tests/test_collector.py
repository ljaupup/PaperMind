from datetime import datetime, timezone

import arxiv

from app.infrastructure.arxiv.collector import result_to_paper


def test_result_to_paper() -> None:
    """arXiv 返回对象应完整转换为项目的 Paper 模型。"""
    result = arxiv.Result(
        entry_id="https://arxiv.org/abs/2401.00001v1",
        published=datetime(2024, 1, 1, tzinfo=timezone.utc),
        title=" Test Paper ",
        authors=[arxiv.Result.Author("Alice"), arxiv.Result.Author("Bob")],
        summary=" This is a test abstract. ",
        links=[
            arxiv.Result.Link(
                href="https://arxiv.org/pdf/2401.00001v1",
                title="pdf",
                content_type="application/pdf",
            )
        ],
    )

    paper = result_to_paper(result)

    assert paper.paper_id == "2401.00001v1"
    assert paper.title == "Test Paper"
    assert paper.authors == ["Alice", "Bob"]
    assert paper.pdf_url == "https://arxiv.org/pdf/2401.00001v1"
    assert paper.parse_status == "metadata_only"
