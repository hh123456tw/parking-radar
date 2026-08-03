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


from analysis import clean_available, district_hell_score, hell_label, hell_score


def test_clean_available_rejects_special_and_impossible_values():
    """官方負數、缺值與剩餘數超過總數都不能進入計算。"""
    assert clean_available(100, -9) is None
    assert clean_available(100, None) is None
    assert clean_available(100, 101) is None
    assert clean_available(0, 0) is None
    assert clean_available(100, 20) == 20


def test_hell_score_and_label_use_fixed_thresholds():
    assert hell_score(200, 10) == 95.0
    assert hell_label(95.0) == "停車地獄"
    assert hell_label(80.0) == "很難停"
    assert hell_label(60.0) == "開始擠"
    assert hell_label(59.9) == "輕鬆停"


def test_district_score_is_weighted_by_spaces():
    rows = [
        {"total_spaces": 100, "available_spaces": 50},
        {"total_spaces": 300, "available_spaces": 30},
        {"total_spaces": 50, "available_spaces": -9},
    ]
    assert district_hell_score(rows) == 80.0


from analysis import distance_ease, haversine_m, rank_candidates, recommendation_score


def test_haversine_and_radius_filter():
    distance = haversine_m(25.0330, 121.5654, 25.0330, 121.5704)
    assert 490 < distance < 520
    rows = [
        {"lot_id": "near", "latitude": 25.0330, "longitude": 121.5704,
         "total_spaces": 100, "available_spaces": 20},
        {"lot_id": "far", "latitude": 25.0330, "longitude": 121.5904,
         "total_spaces": 100, "available_spaces": 50},
    ]
    ranked = rank_candidates(rows, 25.0330, 121.5654)
    assert [row["lot_id"] for row in ranked] == ["near"]


def test_recommendation_weights_with_and_without_history():
    # 即時地獄 80 → 容易度 20；距離 750m → 容易度 50；歷史地獄 60 → 40。
    assert recommendation_score(80, 750, 60) == 33.0
    assert recommendation_score(80, 750, None) == 32.0
    assert distance_ease(1500) == 0.0
