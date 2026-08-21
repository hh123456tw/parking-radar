"""回歸測試：本機 Flask 也必須允許 Service Worker 控制首頁。"""

from app import create_app


def test_service_worker_response_allows_root_scope():
    """localhost 不經 nginx 時，服務器腳本仍要帶根目錄控制標頭。"""
    client = create_app({"TESTING": True}).test_client()

    response = client.get("/static/sw.js")

    assert response.status_code == 200
    assert response.headers["Service-Worker-Allowed"] == "/"
