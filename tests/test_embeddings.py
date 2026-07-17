import pytest

from app.infrastructure.ai.embeddings import HashEmbeddingClient


@pytest.mark.asyncio
async def test_hash_embedding_shape() -> None:
    """验证哈希 Embedding 的输出数量和维度。

    :return: None；通过断言验证预期行为。
    """
    client = HashEmbeddingClient(dimensions=16)
    vectors = await client.embed_texts(["rag retrieval", "database index"])
    assert len(vectors) == 2
    assert len(vectors[0]) == 16
