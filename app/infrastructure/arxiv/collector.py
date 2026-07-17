import asyncio

import arxiv

from app.domain.models import Paper


# 复用客户端，并显示限制请求频率和重试次数，避免短时间连续请求 arXiv
_ARXIV_CLIENT = arxiv.Client(
    page_size=100,
    delay_seconds=3.0,
    num_retries=3,
)


def clean_text(text: str | None) -> str:
    """压缩空白字符，将 arXiv 返回的文本规范为单行内容"""
    if not text:
        return ""
    return " ".join(text.split())


def result_to_paper(result: arxiv.Result) -> Paper:
    """将一个 arxiv.Result 的常用元数据转换为项目内部的 Paper 模型。"""
    authors = [clean_text(author.name) for author in result.authors]
    authors = [name for name in authors if name]
    
    return Paper(
        paper_id=result.get_short_id(),
        title=clean_text(result.title),
        abstract=clean_text(result.summary),
        authors=authors,
        url=result.entry_id,
        pdf_url=result.pdf_url,
        parse_status="metadata_only",
        published=result.published.isoformat(),
    )


def _collect_arxiv_sync(query: str, max_result: int = 5) -> list[Paper]:
    """在线程中调用共享 arXiv 客户端，按提交时间倒序获取论文"""
    search = arxiv.Search(
        query=query,
        max_results=max_result,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending,
    )
    try:
        return [result_to_paper(result) for result in _ARXIV_CLIENT.results(search)]
    except arxiv.HTTPError as exc:
        if exc.status == 429:
            raise RuntimeError(
                "arXiv API 暂时拒绝请求（HTTP 429）。停止重复运行，等待至少 60 秒后再试；"
                "同时确认没有其他脚本或服务在使用同一网络出口请求 arXiv。"
            ) from None
        raise


async def collect_arxiv(query: str, max_result: int = 5) -> list[Paper]:
    """异步采集入口，避免同步 SDK 调用阻塞 FastAPI 事件循环"""
    return await asyncio.to_thread(_collect_arxiv_sync, query, max_result)
