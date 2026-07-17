from app.domain.models import SearchResult
from app.domain.ports import EmbeddingClient, VectorStore

class RetrievalService:
    """协调 Embedding 与向量库，将用户查询转换为 Top-K 检索结果"""

    def __init__(
        self,
        vector_store: VectorStore,
        embedding_client: EmbeddingClient,
    ) -> None:
        """注入向量库和 Embedding Provider

        :param vector_store: 提取向量查询能力的存储对象
        :param embedding_client: 将查询文本转换为向量的客户端
        """
        self.vector_store = vector_store
        self.embedding_client = embedding_client

    async def retrieve(self, query: str, top_k: int = 3) -> list[SearchResult]:
        """嵌入查询文本，再从向量库获取最相关的文本块

        :param query: _description_
        :param top_k: _description_, defaults to 3
        :return: 最多 ``top_k`` 条检索结果
        """
        embedding = (await self.embedding_client.embed_texts([query]))[0]
        return self.vector_store.query(embedding, top_k=top_k)
