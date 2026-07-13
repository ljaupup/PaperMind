import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def env(name: str, default: str = "") -> str:
    """读取环境变量；未设置时返回给定的默认值。"""
    return os.getenv(name, default)


@dataclass(frozen=True)
class Settings:
    """PaperMind 的运行配置，统一从 .env 或系统环境变量读取。"""

    # Provider 与存储实现可以在不修改业务代码的情况下切换。
    llm_provider: str = env("LLM_PROVIDER", "mock")
    embedding_provider: str = env("EMBEDDING_PROVIDER", "hash")
    storage_backend: str = env("STORAGE_BACKEND", "local")

    # LLM Provider 的连接信息；密钥只从环境变量读取，不能写入源码。
    deepseek_api_key: str = env("DEEPSEEK_API_KEY")
    deepseek_base_url: str = env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    deepseek_model: str = env("DEEPSEEK_MODEL", "deepseek-v4-flash")

    siliconflow_api_key: str = env("SILICONFLOW_API_KEY")
    siliconflow_base_url: str = env("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
    siliconflow_model: str = env("SILICONFLOW_MODEL", "Qwen/Qwen2.5-72B-Instruct")
    siliconflow_embedding_model: str = env("SILICONFLOW_EMBEDDING_MODEL", "BAAI/bge-m3")

    # 本地 JSON 存储的文件位置。
    papers_file: Path = Path(env("PAPERS_FILE", "data/papers.json"))

    # ChromaDB Server 连接信息。
    chroma_host: str = env("CHROMA_HOST", "localhost")
    chroma_port: int = int(env("CHROMA_PORT", "8001"))
    collection_name: str = env("COLLECTION_NAME", "papermind")

    # PostgreSQL 连接信息；默认值与 docker-compose.yml 保持一致。
    postgres_host: str = env("POSTGRES_HOST", "localhost")
    postgres_port: int = int(env("POSTGRES_PORT", "5432"))
    postgres_db: str = env("POSTGRES_DB", "papermind")
    postgres_user: str = env("POSTGRES_USER", "papermind")
    postgres_password: str = env("POSTGRES_PASSWORD", "papermind")

    @property
    def postgres_dsn(self) -> str:
        """按 psycopg 接受的格式生成 PostgreSQL 连接字符串。"""
        return (
            f"host={self.postgres_host} "
            f"port={self.postgres_port} "
            f"dbname={self.postgres_db} "
            f"user={self.postgres_user} "
            f"password={self.postgres_password}"
        )