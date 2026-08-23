"""Flask 路由整合測試：保留真實分析流程，只隔離 DB 與外部 API。"""

import logging
from datetime import datetime, timedelta, timezone

import pytest
import requests

import app as app_module
from ai_service import IntentServiceError, ParkingIntent
from calendar_service import classify_arrival_day


class CloseTrackingConnection:
    """記錄每條路由結束後是否關閉資料庫連線。"""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def make_client(**config):
    """建立具有 session 功能的 Flask 測試客戶端。"""
    settings = {
        "TESTING": True, "SECRET_KEY": "test", "AUTO_REFRESH_ENABLED": False,
        "OPENROUTESERVICE_API_KEY": "",
    }
    settings.update(config)
    return app_module.create_app(settings).test_client()


def lot_row(captured_at=None):
    """建立可通過真實推薦與序列化流程的完整場站資料。"""
    return {
        "lot_id": "TPE1", "lot_name": "A場", "district": "信義區",
        "address": "市府路", "operator_type": "民營停車場",
        "total_spaces": 100, "available_spaces": 20,
        "fee_info": "每小時30元", "service_time": "24小時",
        "fare_rules_json": '{"FareRule":[{"ParkingType":"C","RateType":"1",'
                           '"ChargeableSTime":"0800","ChargeableETime":"2200",'
                           '"ParkingRates":"60"}]}',
        "facility_type": "underground", "facility_source": "official",
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


def test_public_candidate_keeps_decision_card_fields():
    row = lot_row()
    row.update({
        "decision_status": "recommended",
        "decision_label": "建議前往",
        "pressure_label": "低",
        "recommendation_label": "高",
        "walking_distance_m": 430.2,
        "walking_duration_minutes": 5.2,
        "reasons": ["目前 20 / 100 格可停", "距目的地近，約 300 公尺"],
    })

    result = app_module.public_candidate(row)

    assert result["address"] == row["address"]
    assert result["total_spaces"] == row["total_spaces"]
    assert result["decision_status"] == "recommended"
    assert result["decision_label"] == "建議前往"
    assert result["pressure_label"] == "低"
    assert result["recommendation_label"] == "高"
    assert result["walking_distance_m"] == 430.2
    assert result["walking_duration_minutes"] == 5.2
    assert result["reasons"] == row["reasons"]


def test_address_query_uses_walking_routes_to_order_safe_lots(monkeypatch):
    """有地址與金鑰時，安全場站要依步行時間排序並輸出步行欄位。"""
    straight_near = lot_row()
    straight_near.update(lot_id="STRAIGHT", lot_name="直線較近",
                         latitude=25.0376, longitude=121.5638)
    walk_near = lot_row()
    walk_near.update(lot_id="WALK", lot_name="步行較近",
                     latitude=25.0390, longitude=121.5660)
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    monkeypatch.setattr(app_module, "geocode_address", lambda *_args: {
        "display_address": "臺北市政府", "latitude": 25.0375,
        "longitude": 121.5637,
    })
    monkeypatch.setattr(
        app_module, "fetch_current_lots", lambda *_args: [straight_near, walk_near])
    monkeypatch.setattr(app_module, "fetch_walking_routes", lambda *_args, **_kwargs: {
        "STRAIGHT": {"walking_distance_m": 850.0,
                     "walking_duration_minutes": 11.0},
        "WALK": {"walking_distance_m": 500.0,
                 "walking_duration_minutes": 6.0},
    })

    response = make_client(OPENROUTESERVICE_API_KEY="test-key").post(
        "/api/query", json={
            "mode": "manual", "address": "臺北市信義區市府路1號",
            "arrival_time": "2026-08-04T18:00:00+08:00",
        })

    body = response.get_json()
    assert response.status_code == 200
    assert [row["lot_id"] for row in body["recommendations"]] == ["WALK", "STRAIGHT"]
    assert body["recommendations"][0]["walking_duration_minutes"] == 6.0


def test_walking_route_failure_keeps_address_query_usable(monkeypatch):
    """步行 API 失敗時不得讓停車查詢失敗，應保留直線距離結果。"""
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    monkeypatch.setattr(app_module, "geocode_address", lambda *_args: {
        "display_address": "臺北市政府", "latitude": 25.0375,
        "longitude": 121.5637,
    })
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args: [lot_row()])
    monkeypatch.setattr(
        app_module, "fetch_walking_routes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            app_module.WalkingRouteError("步行路線服務暫時無法使用")),
    )

    response = make_client(OPENROUTESERVICE_API_KEY="test-key").post(
        "/api/query", json={
            "mode": "manual", "address": "臺北市信義區市府路1號",
            "arrival_time": "2026-08-04T18:00:00+08:00",
        })

    lot = response.get_json()["recommendations"][0]
    assert response.status_code == 200
    assert lot["walking_distance_m"] is None
    assert lot["distance_m"] is not None


def test_address_query_without_route_key_never_calls_walking_api(monkeypatch):
    """未設定金鑰時直接沿用直線距離，不應送出無效外部請求。"""
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    monkeypatch.setattr(app_module, "geocode_address", lambda *_args: {
        "display_address": "臺北市政府", "latitude": 25.0375,
        "longitude": 121.5637,
    })
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args: [lot_row()])
    monkeypatch.setattr(
        app_module, "fetch_walking_routes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("沒有金鑰時不應呼叫步行 API")),
    )

    response = make_client(OPENROUTESERVICE_API_KEY="").post(
        "/api/query", json={
            "mode": "manual", "address": "臺北市信義區市府路1號",
            "arrival_time": "2026-08-04T18:00:00+08:00",
        })

    assert response.status_code == 200
    assert response.get_json()["recommendations"][0]["walking_distance_m"] is None


def test_query_enriches_every_result_with_local_decision_metadata(monkeypatch):
    client = make_client()
    monkeypatch.setattr(app_module, "classify_arrival_day", lambda _arrival: {
        "kind": "holiday", "label": "國定假日｜國慶日",
        "is_holiday": True, "source": "taiwan_calendar",
    })
    monkeypatch.setattr(app_module, "build_fee_summary", lambda *_args: {
        "hourly_fee_label": "60 元／時", "daily_cap_label": "230 元",
        "fee_note": None, "fee_confidence": "exact",
    })
    # Reuse the route's existing database, geocoder, and ranking fakes.
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args: [lot_row()])
    monkeypatch.setattr(app_module, "fetch_matching_history", lambda *_args: [])
    response = client.post("/api/query", json={
        "mode": "manual", "district": "中正區",
        "arrival_time": "2026-10-10T18:00:00+08:00",
    })
    lot = response.get_json()["recommendations"][0]
    assert lot["arrival_day_label"] == "國定假日｜國慶日"
    assert lot["hourly_fee_label"] == "60 元／時"
    assert lot["daily_cap_label"] == "230 元"
    assert lot["facility_type_label"] == "地下停車場"


def test_successful_query_logs_stage_durations_without_destination(monkeypatch, caplog):
    """正常查詢必須留下各階段耗時，但不得把使用者目的地寫進日誌。"""
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args: [lot_row()])

    with caplog.at_level(logging.INFO):
        response = make_client().post("/api/query", json={
            "mode": "manual", "district": "信義區",
            "arrival_time": "2026-08-04T18:00:00+08:00",
        })

    assert response.status_code == 200
    messages = [record.getMessage() for record in caplog.records]
    timing = next(message for message in messages if message.startswith("query_complete "))
    assert "mode=manual" in timing
    assert "parse_ms=" in timing
    assert "geocode_ms=" in timing
    assert "freshness_ms=" in timing
    assert "database_ms=" in timing
    assert "walking_ms=" in timing
    assert "total_ms=" in timing
    assert "信義區" not in timing


def test_query_missing_calendar_file_uses_weekday_fallback(monkeypatch, tmp_path):
    """行事曆檔案缺失時仍以本機規則分類抵達日，並標記 fallback 來源。"""
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args: [lot_row()])
    monkeypatch.setattr(app_module, "fetch_matching_history", lambda *_args: [])
    monkeypatch.setattr(
        app_module, "classify_arrival_day",
        lambda arrival: classify_arrival_day(arrival, calendar_dir=tmp_path),
    )

    response = make_client().post("/api/query", json={
        "mode": "manual", "district": "信義區",
        "arrival_time": "2026-08-04T18:00:00+08:00",
    })
    lot = response.get_json()["recommendations"][0]

    assert lot["arrival_day_label"] == "平日"
    assert lot["calendar_source"] == "weekday_fallback"


def test_query_malformed_fare_rules_shows_official_unknown(monkeypatch):
    """費率規則格式異常時不得臆測金額，時費與上限都顯示官方未標示。"""
    row = lot_row()
    row["fare_rules_json"] = "{broken-json"
    row["fee_info"] = None
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args: [row])
    monkeypatch.setattr(app_module, "fetch_matching_history", lambda *_args: [])
    monkeypatch.setattr(app_module, "classify_arrival_day", lambda _arrival: {
        "kind": "weekday", "label": "平日", "is_holiday": False,
        "source": "weekday_fallback"})

    response = make_client().post("/api/query", json={
        "mode": "manual", "district": "信義區",
        "arrival_time": "2026-08-04T18:00:00+08:00",
    })
    lot = response.get_json()["recommendations"][0]

    assert lot["hourly_fee_label"] == "官方未標示"
    assert lot["daily_cap_label"] == "官方未標示"
    assert lot["fee_confidence"] == "unknown"


def test_query_null_facility_metadata_degrades_to_unknown_type(monkeypatch):
    """沒有型態資料時顯示型態待確認，而不是略過欄位。"""
    row = lot_row()
    row["facility_type"] = None
    row["facility_source"] = None
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args: [row])
    monkeypatch.setattr(app_module, "fetch_matching_history", lambda *_args: [])
    monkeypatch.setattr(app_module, "classify_arrival_day", lambda _arrival: {
        "kind": "weekday", "label": "平日", "is_holiday": False,
        "source": "weekday_fallback"})

    response = make_client().post("/api/query", json={
        "mode": "manual", "district": "信義區",
        "arrival_time": "2026-08-04T18:00:00+08:00",
    })
    lot = response.get_json()["recommendations"][0]

    assert lot["facility_type"] == "unknown"
    assert lot["facility_type_label"] == "型態待確認"
    assert lot["facility_source"] == "unknown"


def test_query_path_makes_no_calendar_or_osm_network_calls(monkeypatch):
    """查詢流程只讀本機行事曆與費率，任何 requests.get 都應失敗。"""
    def raise_get(*_args, **_kwargs):
        raise AssertionError("查詢路徑不得呼叫 calendar 或 OSM 網路")

    monkeypatch.setattr(requests, "get", raise_get)
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args: [lot_row()])
    monkeypatch.setattr(app_module, "fetch_matching_history", lambda *_args: [])

    response = make_client().post("/api/query", json={
        "mode": "manual", "district": "信義區",
        "arrival_time": "2026-08-04T18:00:00+08:00",
    })

    assert response.status_code == 200
    assert response.get_json()["recommendations"][0]["lot_id"] == "TPE1"


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


def test_regular_query_does_not_preload_history(monkeypatch):
    """一般查詢只使用即時資料；歷史留給使用者點擊後的專用端點。"""
    connection = CloseTrackingConnection()
    monkeypatch.setattr(app_module, "get_connection", lambda: connection)
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args: [lot_row()])
    monkeypatch.setattr(
        app_module,
        "fetch_matching_history",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("一般查詢不應預先讀取歷史")
        ),
    )

    response = make_client().post("/api/query", json={
        "mode": "manual", "district": "信義區",
        "arrival_time": "2026-08-04T18:00:00+08:00",
    })

    assert response.status_code == 200
    assert response.get_json()["recommendations"][0]["lot_id"] == "TPE1"


def test_history_intent_loads_history_for_only_three_candidates(monkeypatch):
    """明確詢問歷史時保留分析，但只讀前三座，避免整區大量運算。"""
    requested_lot_ids = []
    requested_range = []
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    monkeypatch.setattr(app_module, "parse_parking_query", lambda *_args: ParkingIntent(
        intent="history", original_destination=None, address=None, district="信義區",
        arrival_time="2026-08-04T18:00:00+08:00", missing_fields=[],
    ))
    rows = []
    for index in range(4):
        row = lot_row()
        row["lot_id"] = f"TPE{index + 1}"
        rows.append(row)
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args: rows)

    def matching_history(_connection, lot_ids, start_utc, end_utc):
        requested_lot_ids.extend(lot_ids)
        requested_range.append(end_utc - start_utc)
        return []

    monkeypatch.setattr(app_module, "fetch_matching_history", matching_history)

    response = make_client().post(
        "/api/query", json={"mode": "chat", "message": "這區歷史上好停嗎？"})

    assert response.status_code == 200
    assert requested_lot_ids == ["TPE1", "TPE2", "TPE3"]
    assert requested_range == [timedelta(days=7)]


def test_query_reads_parking_data_from_connection_opened_after_refresh(monkeypatch):
    """補抓完成後必須用新交易讀取，避免圖卡顯示更新前的空位。"""
    state = {"refreshed": False}

    class SnapshotConnection(CloseTrackingConnection):
        def __init__(self):
            super().__init__()
            # 模擬 MySQL REPEATABLE READ：連線建立後固定看到當時版本。
            self.sees_fresh_data = state["refreshed"]

    monkeypatch.setattr(app_module, "get_connection", SnapshotConnection)

    def refresh_data():
        state["refreshed"] = True
        return "fresh", None

    monkeypatch.setattr(app_module, "ensure_fresh_parking_data", refresh_data)

    def current_lots(connection, *_args):
        row = lot_row()
        row["available_spaces"] = 30 if connection.sees_fresh_data else 1
        return [row]

    monkeypatch.setattr(app_module, "fetch_current_lots", current_lots)
    monkeypatch.setattr(app_module, "fetch_matching_history", lambda *_args: [])
    client = app_module.create_app({
        "TESTING": True, "SECRET_KEY": "test", "AUTO_REFRESH_ENABLED": True,
    }).test_client()

    response = client.post("/api/query", json={
        "mode": "manual", "district": "信義區",
        "arrival_time": "2026-08-05T10:00:00+08:00",
    })

    assert response.status_code == 200
    assert response.get_json()["current"]["district_score"] == 70.0


def test_query_returns_official_and_collection_times_in_taipei(monkeypatch):
    """官方時間與抓取時間都要從 MySQL UTC 正確轉成台北時間。"""
    connection = CloseTrackingConnection()
    row = lot_row(datetime(2026, 8, 3, 10))
    row["snapshot_updated_at"] = datetime(2026, 8, 3, 9, 55)
    monkeypatch.setattr(app_module, "get_connection", lambda: connection)
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args: [row])
    monkeypatch.setattr(app_module, "fetch_matching_history", lambda *_args: [])

    response = make_client().post("/api/query", json={
        "mode": "manual", "district": "信義區",
        "arrival_time": "2026-08-03T18:00:00+08:00",
    })
    body = response.get_json()

    assert response.status_code == 200
    assert body["official_updated_at"] == "2026-08-03T17:55:00+08:00"
    assert body["collected_at"] == "2026-08-03T18:00:00+08:00"
    assert body["updated_at"] == body["collected_at"]


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


def test_chat_landmark_alias_replaces_gemini_district_guess():
    """台北車站使用固定門牌，不沿用 Gemini 可能造成誤判的地標字串。"""
    parsed = app_module.validate_parsed_query({
        "intent": "recommend", "original_destination": "台北車站",
        "address": "台北車站", "district": "中正區",
        "arrival_time": "2026-08-04T18:00:00+08:00", "missing_fields": [],
    })

    assert parsed["address"] == "臺北市中正區北平西路3號"
    assert parsed["destination_label"] == \
        "台北車站（臺北市中正區北平西路3號）"


@pytest.mark.parametrize(("parsed", "expected"), [
    ({"original_destination": "資策會"}, True),
    ({"original_destination": "臺北市信義區市府路1號"}, False),
    ({"original_destination": "台北車站", "destination_label": "台北車站（北平西路3號）"},
     False),
])
def test_fuzzy_landmark_confirmation_rule(parsed, expected):
    """模糊地標需確認；完整門牌與後端固定別名可直接查詢。"""
    assert app_module.requires_location_confirmation(parsed) is expected


def test_chat_ambiguous_landmark_returns_clickable_choices(monkeypatch):
    """多據點地標應先回傳已驗證候選，不執行停車分析。"""
    monkeypatch.setattr(app_module, "parse_parking_query", lambda *_args: ParkingIntent(
        intent="recommend", original_destination="資策會", address="資策會",
        district="松山區", arrival_time=None, missing_fields=[],
        location_candidates=[
            {"name": "資策會數位教育研究所", "address": "臺北市大安區信義路三段153號",
             "district": "大安區"},
            {"name": "資策會數位轉型研究院", "address": "臺北市松山區民生東路四段133號",
             "district": "松山區"},
        ],
    ))
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    monkeypatch.setattr(app_module, "geocode_candidates", lambda *_args: [
        {"name": "資策會數位教育研究所", "address": "臺北市大安區信義路三段153號",
         "district": "大安區", "display_address": "信義路三段153號, 臺北市",
         "latitude": 25.03, "longitude": 121.54},
        {"name": "資策會數位轉型研究院", "address": "臺北市松山區民生東路四段133號",
         "district": "松山區", "display_address": "民生東路四段133號, 臺北市",
         "latitude": 25.06, "longitude": 121.55},
    ])

    client = make_client()
    stale_response = client.post(
        "/api/query", json={"mode": "chat", "message": "我要去資策會"})
    response = client.post(
        "/api/query", json={"mode": "chat", "message": "我要去資策會"},
        headers={"X-Client-Version": "2"})

    data = response.get_json()
    assert stale_response.status_code == 409
    assert "重新整理頁面" in stale_response.get_json()["error"]
    assert response.status_code == 200
    assert data["needs_location_choice"] is True
    assert len(data["location_choices"]) == 2
    assert data["location_choices"][0]["district"] == "大安區"


def test_chat_single_fuzzy_candidate_still_requires_confirmation(monkeypatch):
    """只驗證出一個模糊地標時也不能擅自當成使用者目的地。"""
    monkeypatch.setattr(app_module, "parse_parking_query", lambda *_args: ParkingIntent(
        intent="recommend", original_destination="資策會", address="資策會",
        district="松山區", arrival_time=None, missing_fields=[],
        location_candidates=[{
            "name": "資策會數位轉型研究院",
            "address": "臺北市松山區民生東路四段133號", "district": "松山區",
        }],
    ))
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    monkeypatch.setattr(app_module, "geocode_candidates", lambda *_args: [{
        "name": "資策會數位轉型研究院",
        "address": "臺北市松山區民生東路四段133號", "district": "松山區",
        "display_address": "民生東路四段133號, 臺北市",
        "latitude": 25.06, "longitude": 121.55,
    }])

    response = make_client().post(
        "/api/query", json={"mode": "chat", "message": "我要去資策會"},
        headers={"X-Client-Version": "2"})

    data = response.get_json()
    assert response.status_code == 200
    assert data["needs_location_choice"] is True
    assert len(data["location_choices"]) == 1


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
