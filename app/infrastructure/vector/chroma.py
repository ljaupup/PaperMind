import time

import chromadb

from app.domain.models import PaperChunk, SearchResult

class ChromaVectorStore:
    """ChromaDB Server 的访问封装，负责向量写入与相似度查询"""

    def __init__(self, host: str, port: int, collection_name: str) -> None:
        """连接 ChromaDB，并获取或创建指定 collection

        :param host: ChromaDB Server 主机名
        :param port: ChromaDB Server 端口
        :param collection_name: 要使用的 collection 名称
        """
        last_error: Exception | None = None
        for  _ in range(10):
            try:
                self.client = chromadb.HttpClient(host=host, port=port)
                self.collection = self.client.get_or_create_collection(name=collection_name)
                return
            except Exception as exc:
                last_error = exc
                time.sleep(1)
        raise RuntimeError(f"failed to connect chromadb server: {last_error}")

    def upsert_chunks(self, chunks: list[PaperChunk], embeddings: list[list[float]]) -> None:
        """将文本块、向量和来源元数据按照 chunk_id 幂等写入 ChromaDB

        :return: None: 文本块成功写入后不返回数据
        """
        if not chunks:
            return
        self.collection.upsert(
            ids=[chunk.chunk_id for chunk in chunks],
            documents=[chunk.text for chunk in chunks],
            embeddings=embeddings,
            metadatas=[
                {
                    "paper_id": chunk.paper_id,
                    "title": chunk.title,
                    "url": chunk.url,
                    "pdf_url": chunk.pdf_url or "",
                    "page": chunk.page if chunk.page is not None else -1,
                }
                for chunk in chunks
            ]
        )

    def query(self, embedding: list[float], top_k: int) -> list[SearchResult]:
        """查询最相近的文本块，并转换 ChromaDB 响应

        :return: 按 ChromaDB 距离排序的检索结果
        """
        result = self.collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )

        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]

        items: list[SearchResult] = []
        for doc, meta, distance in zip(documents, metadatas, distances):
            raw_page = int(meta.get("page", -1))
            items.append(
                SearchResult(
                    text=doc,
                    title=str(meta.get("title", "")),
                    url=str(meta.get("url", "")),
                    pdf_url=str(meta.get("pdf_url", "")) or None,
                    page=None if raw_page < 0 else raw_page,
                    score=float(distance)
                )
            )
        return items
