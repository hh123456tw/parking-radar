"""Flask 路由整合測試：保留真實分析流程，只隔離 DB 與外部 API。"""

from datetime import datetime, timezone

import pytest

import app as app_module
from ai_service import IntentServiceError, ParkingIntent


class CloseTrackingConnection:
    """記錄每條路由結束後是否關閉資料庫連線。"""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def make_client():
    """建立具有 session 功能的 Flask 測試客戶端。"""
    return app_module.create_app({"TESTING": True, "SECRET_KEY": "test"}).test_client()


def lot_row(captured_at=None):
    """建立可通過真實推薦與序列化流程的完整場站資料。"""
    return {
        "lot_id": "TPE1", "lot_name": "A場", "district": "信義區",
        "address": "市府路", "operator_type": "民營停車場",
        "total_spaces": 100, "available_spaces": 20,
        "fee_info": "每小時30元", "service_time": "24小時",
        "latitude": 25.0376, "longitude": 121.5638,
        "captured_at": captured_at or datetime(2026, 8, 4, 10, tzinfo=timezone.utc),
    }


def test_health_route_returns_ok_without_dependencies():
    """健康檢查不得依賴資料庫、Gemini 或地址服務。"""
    response = make_client().get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_index_route_renders_single_page():
    """首頁模板至少應可離線渲染，避免部署後只剩 API 可用。"""
    response = make_client().get("/")
    assert response.status_code == 200
    assert "停車地獄雷達" in response.get_data(as_text=True)


def test_history_route_returns_real_series_and_closes_connection(monkeypatch):
    """歷史端點成功時應使用真實時區轉換並關閉連線。"""
    connection = CloseTrackingConnection()
    monkeypatch.setattr(app_module, "get_connection", lambda: connection)
    monkeypatch.setattr(app_module, "fetch_history", lambda *_args: [{
        "captured_at": datetime(2026, 8, 4, 2, 0),
        "total_spaces": 100, "available_spaces": 12,
    }])

    response = make_client().get("/api/parking/TPE1/history")

    assert response.status_code == 200
    assert response.get_json() == {
        "lot_id": "TPE1",
        "points": [{"captured_at": "2026-08-04T10:00:00+08:00",
                    "available_spaces": 12}],
    }
    assert connection.closed is True


def test_history_query_failure_returns_json_and_closes_connection(monkeypatch):
    """連線成功但 SQL 失敗時，歷史端點仍須回傳 JSON 並釋放連線。"""
    connection = CloseTrackingConnection()
    monkeypatch.setattr(app_module, "get_connection", lambda: connection)
    monkeypatch.setattr(
        app_module, "fetch_history",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("query failed")),
    )

    response = make_client().get("/api/parking/TPE1/history")

    assert response.status_code == 503
    assert response.get_json() == {"error": "暫時無法取得歷史資料"}
    assert connection.closed is True


def test_district_only_query_uses_real_history_and_district_ranking(monkeypatch):
    """沒有地址時不得偽造距離，仍應完成行政區推薦。"""
    connection = CloseTrackingConnection()
    monkeypatch.setattr(app_module, "get_connection", lambda: connection)
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args: [lot_row()])
    monkeypatch.setattr(app_module, "fetch_matching_history", lambda *_args: [])

    response = make_client().post("/api/query", json={
        "mode": "manual", "district": "信義區",
        "arrival_time": "2026-08-04T18:00:00+08:00",
    })
    body = response.get_json()

    assert response.status_code == 200
    assert body["destination"] is None
    assert body["recommendations"][0]["distance_m"] is None
    assert body["recommendations"][0]["lot_id"] == "TPE1"
    assert connection.closed is True


def test_query_updated_at_treats_naive_database_time_as_utc(monkeypatch):
    """MySQL 無時區 UTC 要先補 UTC，再輸出臺北時區的資料時間。"""
    connection = CloseTrackingConnection()
    monkeypatch.setattr(app_module, "get_connection", lambda: connection)
    monkeypatch.setattr(app_module, "fetch_current_lots",
                        lambda *_args: [lot_row(datetime(2026, 8, 3, 10))])
    monkeypatch.setattr(app_module, "fetch_matching_history", lambda *_args: [])

    response = make_client().post("/api/query", json={
        "mode": "manual", "district": "信義區",
        "arrival_time": "2026-08-03T18:00:00+08:00",
    })

    assert response.status_code == 200
    assert response.get_json()["updated_at"] == "2026-08-03T18:00:00+08:00"


def test_geocode_miss_returns_district_fallback_before_parking_query(monkeypatch):
    """地址找不到時應回傳 422，且不可繼續查詢候選停車場。"""
    connection = CloseTrackingConnection()
    monkeypatch.setattr(app_module, "get_connection", lambda: connection)
    monkeypatch.setattr(app_module, "geocode_address", lambda *_args: None)
    monkeypatch.setattr(
        app_module, "fetch_current_lots",
        lambda *_args: (_ for _ in ()).throw(AssertionError("不應查停車場")),
    )

    response = make_client().post("/api/query", json={
        "mode": "manual", "address": "不存在的地址",
        "arrival_time": "2026-08-04T18:00:00+08:00",
    })

    assert response.status_code == 422
    assert response.get_json()["fallback"] == "district"
    assert connection.closed is True


def test_chat_follow_up_receives_previous_session_context(monkeypatch):
    """第二句「那週末呢」必須收到第一句保存的目的地與行政區。"""
    contexts = []

    def fake_parse(message, context):
        contexts.append(dict(context))
        return ParkingIntent(
            intent="recommend" if len(contexts) == 1 else "compare",
            original_destination="臺北市政府",
            address="臺北市信義區市府路1號", district="信義區",
            arrival_time="2026-08-08T18:00:00+08:00", missing_fields=[],
        )

    monkeypatch.setattr(app_module, "parse_parking_query", fake_parse)
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    monkeypatch.setattr(app_module, "geocode_address", lambda *_args: {
        "display_address": "臺北市政府", "latitude": 25.0375, "longitude": 121.5637})
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args: [lot_row()])
    monkeypatch.setattr(app_module, "fetch_matching_history", lambda *_args: [])
    client = make_client()

    first = client.post("/api/query", json={"mode": "chat", "message": "我要去臺北市政府"})
    second = client.post("/api/query", json={"mode": "chat", "message": "那週末呢？"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.get_json()["intent"] == "compare"
    assert contexts[0] == {}
    assert contexts[1]["destination"] == "臺北市信義區市府路1號"
    assert contexts[1]["district"] == "信義區"
    assert contexts[1]["lot_id"] == "TPE1"


def test_chat_naive_arrival_time_is_rejected():
    """聊天路徑的無時區抵達時間必須拒絕，避免歷史小時偏移。"""
    with pytest.raises(ValueError, match="抵達時間必須包含時區"):
        app_module.validate_parsed_query({
            "intent": "recommend", "address": "臺北市市府路1號",
            "district": None, "arrival_time": "2026-08-03T18:00:00",
        })


def test_chat_service_failure_returns_manual_fallback(monkeypatch):
    """Gemini 無法使用時，查詢 API 應明確要求改用手動表單。"""
    monkeypatch.setattr(
        app_module, "parse_parking_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(IntentServiceError("失敗")),
    )

    response = make_client().post(
        "/api/query", json={"mode": "chat", "message": "我要去市政府"})

    assert response.status_code == 503
    assert response.get_json() == {"error": "失敗", "fallback": "manual"}
