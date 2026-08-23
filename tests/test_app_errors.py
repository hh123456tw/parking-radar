"""Flask 邊界錯誤測試：確認不合法輸入與資料庫故障仍回傳 JSON。"""

from datetime import datetime, timedelta, timezone

import app as app_module
import pytest


def make_client():
    """建立不向測試程序傳播例外的 Flask 測試客戶端。"""
    flask_app = app_module.create_app({
        "TESTING": False,
        "PROPAGATE_EXCEPTIONS": False,
        "SECRET_KEY": "test",
        "AUTO_REFRESH_ENABLED": False,
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
])
def test_chat_structured_fields_are_validated_before_database(parsed, message):
    """Gemini 結構合法仍可能缺必要值，必須在 DB 查詢前拒絕。"""
    with pytest.raises(ValueError, match=message):
        app_module.validate_parsed_query(parsed)


def test_chat_missing_arrival_time_defaults_to_taipei_now():
    """只說目的地時，以台北現在時間查詢，不要求使用者再補 arrival_time。"""
    now = app_module.datetime.fromisoformat("2026-08-04T19:20:00+08:00")
    parsed = {
        "missing_fields": ["arrival_time"],
        "address": "臺北市政府",
        "district": None,
        "arrival_time": None,
    }

    result = app_module.validate_parsed_query(parsed, now=now)

    assert result["arrival_time"] == now
    assert result["missing_fields"] == []


def test_chat_still_rejects_other_missing_fields_when_time_defaults():
    """自動補現在時間後，地址等真正必要欄位仍不可略過。"""
    now = app_module.datetime.fromisoformat("2026-08-04T19:20:00+08:00")
    parsed = {
        "missing_fields": ["address", "arrival_time"],
        "address": None,
        "district": None,
        "arrival_time": None,
    }

    with pytest.raises(ValueError, match="還需要：address"):
        app_module.validate_parsed_query(parsed, now=now)


def test_original_destination_is_optional_when_district_exists():
    """已有行政區時，僅供顯示的 original_destination 不得阻擋查詢。"""
    parsed = {
        "missing_fields": ["original_destination"],
        "original_destination": "信義區",
        "address": None,
        "district": "信義區",
        "arrival_time": None,
    }

    result = app_module.validate_parsed_query(parsed)

    assert result["district"] == "信義區"
    assert result["address"] is None
    assert result["missing_fields"] == []


def test_unlisted_landmark_original_destination_becomes_geocoding_query():
    """不在固定別名表的單一地標，仍可交給地址服務搜尋。"""
    parsed = {
        "missing_fields": [],
        "original_destination": "華山文創園區",
        "address": None,
        "district": None,
        "arrival_time": None,
    }

    result = app_module.validate_parsed_query(parsed)

    assert result["address"] == "華山文創園區"


def test_chat_combines_district_with_partial_street_address():
    """Gemini 拆開行政區與道路時，地址搜尋仍須收到完整的臺北地址。"""
    parsed = {
        "missing_fields": [],
        "original_destination": "臺北市信義區西村里市府路1號",
        "address": "市府路1號",
        "district": "信義區",
        "arrival_time": None,
    }

    result = app_module.validate_parsed_query(parsed)

    assert result["address"] == "臺北市信義區市府路1號"


def test_chat_known_landmark_uses_fixed_house_address():
    """固定別名比 Gemini 猜測的行政區可靠，應直接轉成已知門牌。"""
    parsed = {
        "missing_fields": [],
        "original_destination": "臺北市政府",
        "address": "臺北市政府",
        "district": "信義區",
        "arrival_time": None,
    }

    result = app_module.validate_parsed_query(parsed)

    assert result["address"] == "臺北市信義區市府路1號"


def test_manual_known_landmark_shares_fixed_address_cache():
    """手動輸入已知地標時，也要轉成固定門牌以共用地址快取。"""
    parsed = app_module.parse_manual_payload({
        "address": "台北車站",
        "arrival_time": "2026-08-23T12:00:00+08:00",
    })

    result = app_module.validate_parsed_query(parsed)

    assert result["address"] == "臺北市中正區北平西路3號"
    assert result["destination_label"] == \
        "台北車站（臺北市中正區北平西路3號）"


def test_fresh_snapshot_skips_on_demand_collector(monkeypatch):
    """45 分鐘內的快照直接使用，不得浪費官方 API 請求。"""
    now = datetime(2026, 8, 4, 6, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        app_module, "_latest_snapshot_time", lambda: now - timedelta(minutes=10))
    monkeypatch.setattr(
        app_module, "collect_once",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("不應更新")),
    )

    assert app_module.ensure_fresh_parking_data(now) == ("fresh", None)


def test_stale_snapshot_returns_immediately_without_collector(monkeypatch):
    """已有舊資料時不得讓使用者等待完整 collector，應立即誠實降級。"""
    now = datetime(2026, 8, 4, 6, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        app_module, "_latest_snapshot_time", lambda: now - timedelta(minutes=60))
    monkeypatch.setattr(
        app_module, "collect_once",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("不應同步更新")),
    )

    assert app_module.ensure_fresh_parking_data(now) == (
        "stale", "資料更新排程尚未完成，目前顯示 60 分鐘前資料")


def test_stale_snapshot_does_not_depend_on_official_api(monkeypatch):
    """已有舊資料時即使官方失敗，也不應在查詢路徑呼叫外部 API。"""
    now = datetime(2026, 8, 4, 6, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        app_module, "_latest_snapshot_time", lambda: now - timedelta(minutes=67))
    monkeypatch.setattr(
        app_module, "collect_once",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("official down")),
    )

    status, notice = app_module.ensure_fresh_parking_data(now)

    assert status == "stale"
    assert notice == "資料更新排程尚未完成，目前顯示 67 分鐘前資料"


def test_failed_refresh_without_any_snapshot_is_unavailable(monkeypatch):
    """完全沒有可降級資料時，回傳明確錯誤而不是空白成功結果。"""
    monkeypatch.setattr(app_module, "_latest_snapshot_time", lambda: None)
    monkeypatch.setattr(
        app_module, "collect_once",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("official down")),
    )

    with pytest.raises(app_module.ParkingDataUnavailable,
                       match="暫時無法取得官方停車資料"):
        app_module.ensure_fresh_parking_data()
