from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    """健康检查接口应返回 HTTP 200 和预期 JSON。"""
    # TestClient 在进程内调用 FastAPI，无需先启动 uvicorn 服务。
    client = TestClient(app)
    response = client.get("/health")
    # assert 条件为假时会抛出 AssertionError，pytest 将该测试标记为 FAILED。
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}