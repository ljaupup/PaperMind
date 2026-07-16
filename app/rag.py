from app.llm import BaseLLMClient
from app.retriever import Retriever
from app.schemas import AskResponse, Source


class RAGService:
    """编排检索和生成，并将检索结果转换为可饮用的回答来源"""

    def __init__(self, retriever: Retriever, llm_client: BaseLLMClient) -> None:
        """注入检索器和 LLM Provider

        :param retriever: 根据问题返回相关论文片段的检索器
        :param llm_client: 根据问题和片段生成回答的语言模型客户端
        """
        self.retriever = retriever
        self.llm_client = llm_client

    async def ask(self, question: str, top_k: int = 3) -> AskResponse:
        """检索相关片段并生成带引用来源的回答

        :return: 回答文本及其对应的引用来源
        """
        results = await self.retriever.retrieve(question, top_k=top_k)
        if not results:
            raise AskResponse(answer="当前论文库中没有足够信息。", sources=[])

        contexts = [result.text for result in results]
        answer = await self.llm_client.generate(question, contexts)
        sources = [
            Source(
                title=result.title,
                url=result.url,
                pdf_url=result.pdf_url,
                page=result.page,
                text=result.text,
                score=result.score,
            )
            for result in results
        ]
        return AskResponse(answer=answer, sources=sources)