"""Flask 邊界錯誤測試：確認不合法輸入與資料庫故障仍回傳 JSON。"""

import app as app_module
import pytest


def make_client():
    """建立不向測試程序傳播例外的 Flask 測試客戶端。"""
    flask_app = app_module.create_app({
        "TESTING": False,
        "PROPAGATE_EXCEPTIONS": False,
        "SECRET_KEY": "test",
    })
    return flask_app.test_client()


def fail_connection():
    """模擬 MySQL 在建立連線階段就無法使用。"""
    raise RuntimeError("mysql unavailable")


def test_query_database_connection_failure_returns_json_503(monkeypatch):
    """查詢 API 的 DB 連線失敗不得落入 Flask HTML 500。"""
    monkeypatch.setattr(app_module, "get_connection", fail_connection)

    response = make_client().post("/api/query", json={
        "mode": "manual",
        "district": "信義區",
        "arrival_time": "2026-08-04T18:00:00+08:00",
    })

    assert response.status_code == 503
    assert response.is_json
    assert response.get_json() == {"error": "服務暫時無法使用，請稍後再試"}


def test_history_database_connection_failure_returns_json_503(monkeypatch):
    """歷史 API 的 DB 連線失敗也必須使用同一種 JSON 錯誤格式。"""
    monkeypatch.setattr(app_module, "get_connection", fail_connection)

    response = make_client().get("/api/parking/TPE0001/history")

    assert response.status_code == 503
    assert response.is_json
    assert response.get_json() == {"error": "暫時無法取得歷史資料"}


def test_query_rejects_non_object_json_with_400():
    """JSON array 沒有欄位名稱，應視為使用者輸入錯誤而不是伺服器故障。"""
    response = make_client().post("/api/query", json=["not", "an", "object"])

    assert response.status_code == 400
    assert response.is_json
    assert response.get_json() == {"error": "JSON 內容必須是物件"}


@pytest.mark.parametrize(("payload", "message"), [
    ({"mode": "manual", "arrival_time": "2026-08-04T18:00:00+08:00"},
     "請輸入地址或選擇行政區"),
    ({"mode": "manual", "district": "板橋區",
      "arrival_time": "2026-08-04T18:00:00+08:00"},
     "只支援臺北市十二行政區"),
    ({"mode": "manual", "district": "信義區",
      "arrival_time": "2026-08-04T18:00:00"},
     "抵達時間必須包含時區"),
])
def test_manual_query_validation_returns_json_400(payload, message):
    """手動表單缺欄位、跨縣市或無時區時都應回傳明確 JSON 400。"""
    response = make_client().post("/api/query", json=payload)

    assert response.status_code == 400
    assert response.is_json
    assert response.get_json() == {"error": message}


@pytest.mark.parametrize(("parsed", "message"), [
    ({"missing_fields": ["address"], "arrival_time": "2026-08-04T18:00:00+08:00"},
     "還需要：address"),
    ({"missing_fields": [], "address": None, "district": None,
      "arrival_time": "2026-08-04T18:00:00+08:00"},
     "請提供臺北市地址或行政區"),
    ({"missing_fields": [], "address": "臺北市政府", "district": None,
      "arrival_time": None},
     "請提供預計抵達時間"),
])
def test_chat_structured_fields_are_validated_before_database(parsed, message):
    """Gemini 結構合法仍可能缺必要值，必須在 DB 查詢前拒絕。"""
    with pytest.raises(ValueError, match=message):
        app_module.validate_parsed_query(parsed)
