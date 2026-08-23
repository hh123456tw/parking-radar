"""OpenRouteService 步行矩陣邊界測試；外部 HTTP 只在這一層替換。"""

import requests
import pytest

from walking_service import WalkingRouteError, fetch_walking_routes


class FakeResponse:
    """模擬 requests 回應，保留狀態檢查與 JSON 解析行為。"""

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_fetch_walking_routes_uses_one_foot_matrix_request():
    """多座停車場必須合併成一次步行矩陣請求並正確對回 lot_id。"""
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return FakeResponse({
            "distances": [[430.2], [850.0]],
            "durations": [[310.0], [600.0]],
        })

    rows = [
        {"lot_id": "A", "latitude": 25.04, "longitude": 121.51},
        {"lot_id": "B", "latitude": 25.05, "longitude": 121.52},
    ]

    result = fetch_walking_routes(
        rows, 25.0478, 121.5170, "secret", timeout=3, post=fake_post)

    assert result == {
        "A": {"walking_distance_m": 430.2, "walking_duration_minutes": 5.2},
        "B": {"walking_distance_m": 850.0, "walking_duration_minutes": 10.0},
    }
    assert captured == {
        "url": "https://api.heigit.org/openrouteservice/v2/matrix/foot-walking",
        "headers": {"Authorization": "secret", "Content-Type": "application/json"},
        "json": {
            "locations": [
                [121.51, 25.04], [121.52, 25.05], [121.517, 25.0478],
            ],
            "sources": ["0", "1"],
            "destinations": ["2"],
            "metrics": ["distance", "duration"],
            "resolve_locations": False,
        },
        "timeout": 3,
    }


def test_fetch_walking_routes_wraps_timeout_for_safe_fallback():
    """路線服務逾時要轉成可辨識錯誤，讓查詢流程退回直線距離。"""
    def timeout_post(*_args, **_kwargs):
        raise requests.Timeout("slow")

    with pytest.raises(WalkingRouteError, match="步行路線服務暫時無法使用"):
        fetch_walking_routes(
            [{"lot_id": "A", "latitude": 25.04, "longitude": 121.51}],
            25.0478, 121.5170, "secret", post=timeout_post)


def test_fetch_walking_routes_skips_unreachable_matrix_cells():
    """單一場站無法建立步行路線時只略過該場，不得破壞其他結果。"""
    def fake_post(*_args, **_kwargs):
        return FakeResponse({
            "distances": [[None], [500.0]],
            "durations": [[None], [360.0]],
        })

    rows = [
        {"lot_id": "A", "latitude": 25.04, "longitude": 121.51},
        {"lot_id": "B", "latitude": 25.05, "longitude": 121.52},
    ]

    assert fetch_walking_routes(
        rows, 25.0478, 121.5170, "secret", post=fake_post) == {
            "B": {"walking_distance_m": 500.0, "walking_duration_minutes": 6.0},
        }


def test_fetch_walking_routes_wraps_invalid_candidate_coordinate():
    """即使上游意外傳入壞座標，也要安全退回直線模式而不是讓路由 503。"""
    with pytest.raises(WalkingRouteError, match="步行路線服務暫時無法使用"):
        fetch_walking_routes(
            [{"lot_id": "A", "latitude": "bad", "longitude": 121.51}],
            25.0478, 121.5170, "secret")
