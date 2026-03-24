"""
应用健康检查测试。
"""

from fastapi.testclient import TestClient


def test_health_endpoint_still_available(client: TestClient):
    """引入基础设施脚手架后，健康检查接口仍应可用。"""
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
