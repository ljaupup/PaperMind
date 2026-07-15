import hashlib
import math
import re
from abc import ABC, abstractmethod


import httpx

from app.config import Settings


class BaseEmbeddingClient(ABC):
    """Embedding Provider 的抽象契约：将一批文本映射为等长向量"""

    @abstractmethod
    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """将输入文本转换为向量。

        :return: 与输入文本一一对应的等长向量列表。
        """
        pass


class HashEmbeddingClient(BaseEmbeddingClient):
    """离线、可复现的哈希向量实现，仅用于 MVP 联调和测试"""

    def __init__(self, dimensions: int = 128) -> None:
        """设置输出向量维度

        :param dimensions: 每条向量包含的浮点数数量, defaults to 128
        """
        self.dimensions = dimensions

    def _embed_one(self, text: str) -> list[float]:
        """将单条文本映射到固定维度并做 L2 归一化

        :return: 长度为 ``self.dimensions`` 的向量。
        """
        vector = [0.0] * self.dimensions
        tokens = re.findall(r"[a-zA-Z0-9_\u4e00-\u9fff]+", text.lower())
        for token in tokens:
            digest = hashlib.md5(token.encode("utf-8")).hexdigest()
            index = int(digest[:8], 16) % self.dimensions
            vector[index] += 1.0

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]


class SiliconFlowEmbeddingClient(BaseEmbeddingClient):
    """通过 SiliconFlow OpenAI 兼容接口调用真实 Embeding 模型"""

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        """保存认证信息、服务地址和模型名

        :param api_key: SiliconFlow APi 密钥
        :param base_url: OpenAI 兼容 API 的基础地址
        :param model: 要调用的 Embedding 模型标识
        """
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """请求远程 Embedding API，并提取响应中的向量

        :return: 服务返回的、与输入顺序一致的向量列表
        """
        if not self.api_key:
            raise RuntimeError("SILICONFLOW_API_KEY is required")

        payload = {"model": self.model, "input": texts}
        headers = {"Authorization": f"Bearer {self.api_key}"}

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                json=payload,
                headers=headers
            )
            response.raise_for_status()

        data = response.json()["data"]
        return [item["embedding"] for item in data]


def create_embedding_client(settings: Settings) -> BaseEmbeddingClient:
    """根据配置选择 Embedding Provider

    :return: SiliconFlow 客户端或本地哈希客户端
    """
    if settings.embedding_provider == "siliconflow":
        return SiliconFlowEmbeddingClient(
            api_key=settings.siliconflow_api_key,
            base_url=settings.siliconflow_base_url,
            model=settings.siliconflow_embedding_model,
        )
    return HashEmbeddingClient()




# class BaseNotifier(ABC):
#     """定义发送通知所需的共同能力"""

#     @abstractmethod
#     def send(self, message: str) -> str:
#         """发送一条通知。

#         :return: 已发送的通知内容
#         """
#         pass


# class ConsoleNotifier(BaseNotifier):
#     """将通知输出到控制台"""

#     def send(self, message: str) -> str:
#         print(message)
#         return message