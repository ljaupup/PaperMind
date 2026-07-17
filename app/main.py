from fastapi import FastAPI

from app.api.router import router
from app.core.container import AppContainer


def create_app(container: AppContainer | None = None) -> FastAPI:
    """创建 HTTP 应用，并允许测试注入替代依赖容器。"""
    app = FastAPI(title="PaperMind", version="0.1.0")
    app.state.container = container or AppContainer()
    app.include_router(router)
    return app


app = create_app()
