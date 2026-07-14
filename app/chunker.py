from app.schemas import Paper, PaperChunk

def chunk_text(text: str, chunk_size: int = 700, overlap: int = 120) -> list[str]:
    """将规范化文本按固定长度切块，并保留相邻块的重叠内容"""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be >=0 and < chunk_size")

    text = " ".join(text.split())
    if not text:
        return []
    
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        # 回退 overlap 个字符，使相邻 chunk 保留上下文连续性
        start = end - overlap
    return chunks


def build_chunks(papers: list[Paper]) -> list[PaperChunk]:
    """将论文标题和摘要转为携带来源元数据的 PaperChunk 列表"""
    chunks: list[PaperChunk] = []
    for paper in papers:
        full_text = f"{paper.title}\n{paper.abstract}"
        for index, text in enumerate(chunk_text(full_text)):
            chunks.append(
                PaperChunk(
                    chunk_id=f"{paper.paper_id}-{index}",
                    paper_id=paper.paper_id,
                    title=paper.title,
                    url=paper.url,
                    pdf_url=paper.pdf_url,
                    page=None,
                    text=text,
                )
            )
    return chunks