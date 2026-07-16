from abc import ABC, abstractmethod

import httpx

from app.config import Settings


class BaseLLMClient(ABC):
    """LLM Provider 的抽象契约，输入问题和检索上下文，输出回答文本"""

    @abstractmethod
    async def generate(self, question: str, contexts: list[str]) -> str:
        """基于问题和论文片段生成回答

        :return: 根据问题和上下文生成回答文本
        """
        pass


class MockLLMClient(BaseLLMClient):
    """不访问网络的确定性 LLM 替身，用于本地开发和测试"""

    async def generate(self, question: str, contexts: list[str]) -> str:
        """返回包含问题和上下文摘要的 mock 回答

        :return: 可预测的本地 mock 回答
        """
        joined = "\n".join(contexts[:2])
        return (
            f"这是 mock 模式下的回答。问题是：{question}\n\n"
            f"我检索到了 {len(contexts)} 条上下文。前两条上下文摘要: \n{joined[:500]}"
        )
    

class OpenAICompatibleLLMClient(BaseLLMClient):
    """调用 OpenAI Chat Completions 兼容接口的通用 LLM 客户端"""

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        """保存认证信息、兼容 API 地址和目标模型

        :param api_key: LLM Provider 的 API 密钥
        :param base_url: OpenAI Chat Completions 兼容 API 的基础地址
        :param model: 要调用的模型标识
        """
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

        ""

    async def generate(self, question: str, contexts: list[str]) -> str:
        """构建 RAG 提示词，调用远程模型并返回第一条回答

        :return: 远程模型响应中的第一条回答文本
        """
        if not self.api_key:
            raise RuntimeError("API key is required for real LLM provider")

        prompt = build_rag_prompt(question, contexts)
        payload = {
            "model": self.model,
            "message": [
                {"role": "system", "content": "你是一个严谨的论文阅读助手"},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}

        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()

        data = response.json()
        return data["choices"][0]["message"]["content"]


def build_rag_prompt(question: str, contexts: list[str]) -> str:
    """将检索片段编号后组合为 RAG 提示词

    :return: 要求模型基于给定来源回答的问题提示词
    """
    context_text = "\n\n".join(
        f"[片段 {index + 1}]\n{context}"
        for index, context in enumerate(contexts)
    )
    return f"""请只基于给定论文片段回答问题。
如果片段中没有足够信息，请明确说明“当前论文库中没有足够信息”。

论文片段：
{context_text}

用户问题：
{question}

请用中文回答，避免编造论文中没有的信息。"""


def create_llm_client(settings: Settings) -> BaseLLMClient:
    """根据配置创建 LLM Provider

    :return: DeepSeek、SiliconFlow 或本地 mock 客户端
    """
    if settings.llm_provider == "deepseek":
        return OpenAICompatibleLLMClient(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
        )
    if settings.llm_provider == "siliconflow":
        return OpenAICompatibleLLMClient(
            api_key=settings.siliconflow_api_key,
            base_url=settings.siliconflow_base_url,
            model=settings.siliconflow_model,
        )
    return MockLLMClient()    