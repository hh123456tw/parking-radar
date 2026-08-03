"""停車地獄雷達核心測試；所有外部服務都使用固定資料或 mock。"""

from app import create_app
from config import Config


def test_config_has_locked_analysis_constants():
    """搜尋半徑與新鮮度必須符合設計規格，不受本機環境影響。"""
    assert Config.SEARCH_RADIUS_M == 1500
    assert Config.FRESHNESS_MINUTES == 45


def test_health_route_returns_ok():
    """健康檢查不依賴資料庫或外部 API。"""
    app = create_app({"TESTING": True, "SECRET_KEY": "test"})
    response = app.test_client().get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
