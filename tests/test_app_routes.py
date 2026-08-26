"""Flask 路由整合測試：保留真實分析流程，只隔離 DB 與外部 API。"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
import requests

import app as app_module


def test_production_app_emits_info_performance_logs():
    """正式環境必須允許 INFO，否則 query_complete 分段耗時不會出現在日誌。"""
    flask_app = app_module.create_app({"TESTING": False})

    assert flask_app.logger.isEnabledFor(logging.INFO)
from ai_service import IntentServiceError, ParkingIntent
from calendar_service import classify_arrival_day


class CloseTrackingConnection:
    """記錄每條路由結束後是否關閉資料庫連線。"""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class SpyCursor:
    """記錄真實 database.fetch_current_lots 送出的 SQL 與參數。"""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class SpyConnection:
    """提供 fetch_current_lots 所需的最小 cursor 介面。"""

    def __init__(self, rows=None):
        self.spy_cursor = SpyCursor(rows)

    def cursor(self):
        return self.spy_cursor

    def close(self):
        pass


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


def test_team_analytics_mode_hides_choice_and_privacy_notice():
    """團隊模式預設分析，不顯示選擇介面或頁尾隱私說明。"""
    response = make_client(ANALYTICS_REQUIRE_CONSENT=False).get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'data-analytics-require-consent="0"' in body
    assert 'id="analytics-consent"' not in body
    assert 'id="analytics-choice"' not in body
    assert 'id="privacy-note"' not in body
    assert "團隊測試期間分析預設啟用" not in body


def test_public_analytics_mode_keeps_original_opt_in_controls():
    """恢復公開模式時，原本的允許、拒絕與更改選擇功能必須完整保留。"""
    response = make_client(ANALYTICS_REQUIRE_CONSENT=True).get("/")
    body = response.get_data(as_text=True)

    assert 'data-analytics-require-consent="1"' in body
    assert 'id="analytics-consent"' in body
    assert 'id="analytics-accept"' in body
    assert 'id="analytics-decline"' in body
    assert 'id="analytics-choice"' in body


def test_index_route_injects_server_owned_city_options():
    """首頁城市選項必須由伺服器依旗標注入，前端不能自行猜測城市。"""
    off_body = make_client(NEW_TAIPEI_ENABLED=False).get("/").get_data(as_text=True)
    on_body = make_client(NEW_TAIPEI_ENABLED=True).get("/").get_data(as_text=True)

    def city_codes(body):
        match = re.search(
            r'<script id="city-options" type="application/json">(.*?)</script>',
            body, re.DOTALL)
        assert match is not None
        return [option["code"] for option in json.loads(match.group(1))]

    assert city_codes(off_body) == ["taipei"]
    assert "新北市" not in off_body
    assert city_codes(on_body) == ["taipei", "new_taipei"]
    assert "新北市" in on_body


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


def test_taipei_station_characterization_keeps_top_order(monkeypatch):
    """台北車站地址查詢必須維持近場站優先的既有排序合約。"""
    near = lot_row()
    near.update(lot_id="NEAR", latitude=25.0477, longitude=121.5169)
    far = lot_row()
    far.update(lot_id="FAR", latitude=25.0520, longitude=121.5230)
    rows = [near, far]
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    monkeypatch.setattr(app_module, "geocode_address", lambda *_args: {
        "display_address": "臺北車站, 臺北市", "latitude": 25.0478,
        "longitude": 121.5170, "city": "taipei", "district": "中正區"})
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args, **_kwargs: rows)
    response = make_client().post("/api/query", json={
        "mode": "manual", "address": "台北車站",
        "arrival_time": "2026-08-26T18:00:00+08:00"})
    body = response.get_json()
    assert response.status_code == 200
    assert [row["lot_id"] for row in body["recommendations"]][:2] == ["NEAR", "FAR"]


class RotatingRowsConnection(SpyConnection):
    """每次 cursor() 回傳下一組資料列，模擬逐來源查詢的結果。"""

    def __init__(self, row_groups):
        self.row_groups = list(row_groups)
        self.spy_cursor = SpyCursor(self.row_groups.pop(0))

    def cursor(self):
        self.spy_cursor.rows = (
            self.row_groups.pop(0) if self.row_groups else [])
        return self.spy_cursor


def test_address_query_can_return_cross_border_lots(monkeypatch):
    """地址查詢的 1.5 公里圓可能跨市，必須能同時回傳雙北場站。"""
    taipei = lot_row()
    taipei.update(lot_id="TPE", city="臺北市", source="taipei",
                  latitude=25.0150, longitude=121.4650,
                  captured_at=datetime(2026, 8, 26, 10, tzinfo=timezone.utc))
    taipei["snapshot_updated_at"] = datetime(
        2026, 8, 26, 9, 55, tzinfo=timezone.utc)
    new_taipei = lot_row()
    new_taipei.update(lot_id="NTP:1", city="新北市", source="new_taipei",
                      district="板橋區", latitude=25.0130, longitude=121.4620,
                      captured_at=datetime(2026, 8, 26, 10, tzinfo=timezone.utc))
    rows = [taipei, new_taipei]
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    monkeypatch.setattr(app_module, "geocode_address", lambda *_args: {
        "display_address": "板橋車站, 板橋區, 新北市", "latitude": 25.0143,
        "longitude": 121.4638, "city": "new_taipei", "district": "板橋區"})
    monkeypatch.setattr(app_module, "fetch_current_lots",
                        lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(app_module, "fetch_latest_snapshot_times",
                        lambda _connection: {
                            "taipei": datetime.now(timezone.utc),
                            "new_taipei": datetime.now(timezone.utc)})
    monkeypatch.setattr(app_module, "fetch_matching_history", lambda *_args: [])

    response = make_client(NEW_TAIPEI_ENABLED=True).post("/api/query", json={
        "mode": "manual", "city": "new_taipei", "address": "板橋車站",
        "arrival_time": "2026-08-26T18:00:00+08:00"})

    body = response.get_json()
    assert response.status_code == 200
    visible = body["recommendations"] + body["other_recommended"]
    assert {row["city"] for row in visible} == {"臺北市", "新北市"}
    for row in visible:
        assert row["city"] and row["source"] and row["data_time_label"]
    taipei_lot = next(row for row in visible if row["source"] == "taipei")
    assert taipei_lot["data_time_label"] == \
        "臺北市官方資料時間 2026-08-26T17:55:00+08:00"
    new_taipei_lot = next(row for row in visible if row["source"] == "new_taipei")
    assert new_taipei_lot["data_time_label"] == \
        "新北市系統取得時間 2026-08-26T18:00:00+08:00"
    sources = {entry["source"]: entry for entry in body["data_sources"]}
    assert sources["taipei"] == {
        "source": "taipei", "city": "臺北市", "status": "fresh",
        "time_kind": "official",
        "collected_at": "2026-08-26T18:00:00+08:00",
        "official_updated_at": "2026-08-26T17:55:00+08:00",
    }
    assert sources["new_taipei"] == {
        "source": "new_taipei", "city": "新北市", "status": "fresh",
        "time_kind": "collected",
        "collected_at": "2026-08-26T18:00:00+08:00",
        "official_updated_at": None,
    }


def test_address_query_bounds_freshness_per_source(monkeypatch):
    """新鮮城市維持 45 分鐘門檻，過期城市才允許舊資料降級。"""
    taipei = lot_row()
    taipei.update(lot_id="TPE", city="臺北市", source="taipei",
                  latitude=25.0150, longitude=121.4650)
    stale_new_taipei = lot_row()
    stale_new_taipei.update(lot_id="NTP:1", city="新北市", source="new_taipei",
                            district="板橋區", latitude=25.0130,
                            longitude=121.4620)
    connection = RotatingRowsConnection([[taipei], [stale_new_taipei]])
    monkeypatch.setattr(app_module, "get_connection", lambda: connection)
    monkeypatch.setattr(app_module, "geocode_address", lambda *_args: {
        "display_address": "板橋車站, 板橋區, 新北市", "latitude": 25.0143,
        "longitude": 121.4638, "city": "new_taipei", "district": "板橋區"})
    monkeypatch.setattr(app_module, "fetch_latest_snapshot_times",
                        lambda _connection: {
                            "taipei": datetime.now(timezone.utc),
                            "new_taipei": datetime.now(timezone.utc)
                            - timedelta(hours=2)})
    monkeypatch.setattr(app_module, "fetch_matching_history", lambda *_args: [])

    response = make_client(NEW_TAIPEI_ENABLED=True).post("/api/query", json={
        "mode": "manual", "city": "new_taipei", "address": "板橋車站",
        "arrival_time": "2026-08-26T18:00:00+08:00"})

    calls = connection.spy_cursor.calls
    assert calls[0][1] == (45, "臺北市")
    assert "UTC_TIMESTAMP()" in calls[0][0]
    assert calls[1][1] == ("新北市",)
    assert "UTC_TIMESTAMP()" not in calls[1][0]
    body = response.get_json()
    assert response.status_code == 200
    sources = {entry["source"]: entry for entry in body["data_sources"]}
    assert sources["taipei"]["status"] == "fresh"
    assert sources["new_taipei"]["status"] == "stale"
    assert body["data_status"] == "stale"


def test_district_query_only_selected_city_and_district(monkeypatch):
    """行政區查詢只送選定城市與行政區，不得跨市查詢。"""
    connection = SpyConnection([lot_row()])
    monkeypatch.setattr(app_module, "get_connection", lambda: connection)
    monkeypatch.setattr(app_module, "fetch_latest_snapshot_times",
                        lambda _connection: {
                            "new_taipei": datetime.now(timezone.utc)})
    monkeypatch.setattr(app_module, "fetch_matching_history", lambda *_args: [])

    response = make_client(NEW_TAIPEI_ENABLED=True).post("/api/query", json={
        "mode": "manual", "city": "new_taipei", "district": "板橋區",
        "arrival_time": "2026-08-26T18:00:00+08:00"})

    sql, params = connection.spy_cursor.calls[0]
    assert "AND city = %s" in sql
    assert "AND district = %s" in sql
    assert params == (45, "新北市", "板橋區")
    body = response.get_json()
    assert response.status_code == 200
    sources = {entry["source"]: entry for entry in body["data_sources"]}
    assert list(sources) == ["new_taipei"]


def test_taipei_fresh_does_not_hide_stale_new_taipei(monkeypatch):
    """單一來源新鮮不得讓另一來源的過期資料被標成新鮮。"""
    connection = CloseTrackingConnection()
    monkeypatch.setattr(app_module, "get_connection", lambda: connection)
    monkeypatch.setattr(app_module, "fetch_latest_snapshot_times",
                        lambda _connection: {
                            "taipei": datetime.now(timezone.utc),
                            "new_taipei": datetime.now(timezone.utc)
                            - timedelta(hours=2)})

    statuses = app_module.parking_data_status({"taipei", "new_taipei"})

    assert statuses["taipei"]["status"] == "fresh"
    assert statuses["new_taipei"]["status"] == "stale"
    assert connection.closed is True


def test_parking_data_status_marks_missing_source(monkeypatch):
    """完全沒有快照的來源必須誠實標示 missing，不能偽裝成 fresh。"""
    connection = CloseTrackingConnection()
    monkeypatch.setattr(app_module, "get_connection", lambda: connection)
    monkeypatch.setattr(app_module, "fetch_latest_snapshot_times",
                        lambda _connection: {
                            "taipei": datetime.now(timezone.utc)})

    statuses = app_module.parking_data_status({"taipei", "new_taipei"})

    assert statuses["new_taipei"]["status"] == "missing"


def test_flag_off_rejects_geocoder_inferred_new_taipei_before_query(monkeypatch):
    """payload 沒帶 city 但地理服務驗證為新北時，旗標關閉必須在查詢前 400 拒絕。"""
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    monkeypatch.setattr(app_module, "geocode_address", lambda *_args: {
        "display_address": "板橋車站, 板橋區, 新北市", "latitude": 25.0143,
        "longitude": 121.4638, "city": "new_taipei", "district": "板橋區"})
    monkeypatch.setattr(
        app_module, "fetch_current_lots",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("旗標關閉時不應查詢任何停車場")),
    )

    response = make_client(NEW_TAIPEI_ENABLED=False).post("/api/query", json={
        "mode": "manual", "address": "新北市板橋區中山路一段152號",
        "arrival_time": "2026-08-26T18:00:00+08:00"})

    assert response.status_code == 400
    assert response.get_json()["error"] == "新北市停車資料尚未開放"


def test_flag_off_rejects_chosen_new_taipei_candidate_before_query(monkeypatch):
    """聊天候選目的地驗證為新北時，旗標關閉必須沿用同一 400 守門。"""
    monkeypatch.setattr(
        app_module, "parse_parking_query",
        lambda *_args, **_kwargs: ParkingIntent(
            intent="recommend",
            original_destination="新北市板橋區中山路一段152號",
            address="新北市板橋區中山路一段152號",
            city=None, district=None,
            arrival_time="2026-08-26T18:00:00+08:00", missing_fields=[],
            location_candidates=[{
                "name": "板橋車站",
                "address": "新北市板橋區中山路一段152號",
            }],
        ),
    )
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    monkeypatch.setattr(app_module, "geocode_candidates", lambda *_args: [{
        "name": "板橋車站", "address": "新北市板橋區中山路一段152號",
        "city": "new_taipei", "district": "板橋區",
        "display_address": "板橋車站, 板橋區, 新北市",
        "latitude": 25.0143, "longitude": 121.4638,
    }])
    monkeypatch.setattr(
        app_module, "fetch_current_lots",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("旗標關閉時不應查詢任何停車場")),
    )

    response = make_client(NEW_TAIPEI_ENABLED=False).post(
        "/api/query", json={"mode": "chat", "message": "我要去板橋車站"})

    assert response.status_code == 400
    assert response.get_json()["error"] == "新北市停車資料尚未開放"


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
        app_module, "fetch_current_lots", lambda *_args, **_kwargs: [straight_near, walk_near])
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


def test_address_query_returns_all_safe_lots_and_only_risk_counts(monkeypatch):
    """API 保留首選以外的安全場站，風險場站不再作為固定展示清單。"""
    rows = []
    for index in range(5):
        row = lot_row()
        row.update(
            lot_id=f"SAFE-{index}", lot_name=f"安全場站 {index}",
            latitude=25.0376 + index * 0.0001,
            available_spaces=20,
        )
        rows.append(row)
    warning = lot_row()
    warning.update(lot_id="WARNING", available_spaces=5, latitude=25.0382)
    avoid = lot_row()
    avoid.update(lot_id="AVOID", available_spaces=2, latitude=25.0383)
    rows.extend([warning, avoid])

    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    monkeypatch.setattr(app_module, "geocode_address", lambda *_args: {
        "display_address": "臺北市政府", "latitude": 25.0375,
        "longitude": 121.5637,
    })
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args, **_kwargs: rows)

    response = make_client().post("/api/query", json={
        "mode": "manual", "address": "臺北市信義區市府路1號",
        "arrival_time": "2026-08-04T18:00:00+08:00",
    })

    body = response.get_json()
    assert response.status_code == 200
    assert len(body["recommendations"]) == 3
    assert len(body["other_recommended"]) == 2
    assert body["warning"] == []
    assert body["recommended_count"] == 5
    assert body["excluded_count"] == 1
    assert "nearest" not in body
    assert "avoid" not in body


def test_walking_route_failure_keeps_address_query_usable(monkeypatch):
    """步行 API 失敗時不得讓停車查詢失敗，應保留直線距離結果。"""
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    monkeypatch.setattr(app_module, "geocode_address", lambda *_args: {
        "display_address": "臺北市政府", "latitude": 25.0375,
        "longitude": 121.5637,
    })
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args, **_kwargs: [lot_row()])
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
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args, **_kwargs: [lot_row()])
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
        "hourly_fee_label": "60 元／時", "hourly_fee_value": 60,
        "daily_cap_label": "230 元",
        "fee_note": None, "fee_confidence": "exact",
    })
    # Reuse the route's existing database, geocoder, and ranking fakes.
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args, **_kwargs: [lot_row()])
    monkeypatch.setattr(app_module, "fetch_matching_history", lambda *_args: [])
    response = client.post("/api/query", json={
        "mode": "manual", "district": "中正區",
        "arrival_time": "2026-10-10T18:00:00+08:00",
    })
    lot = response.get_json()["recommendations"][0]
    assert lot["arrival_day_label"] == "國定假日｜國慶日"
    assert lot["hourly_fee_label"] == "60 元／時"
    assert lot["hourly_fee_value"] == 60
    assert lot["daily_cap_label"] == "230 元"
    assert lot["facility_type_label"] == "地下停車場"


def test_successful_query_logs_stage_durations_without_destination(monkeypatch, caplog):
    """正常查詢必須留下各階段耗時，但不得把使用者目的地寫進日誌。"""
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args, **_kwargs: [lot_row()])

    with caplog.at_level(logging.INFO):
        response = make_client().post("/api/query", json={
            "mode": "manual\nforged", "district": "信義區",
            "arrival_time": "2026-08-04T18:00:00+08:00",
        })

    assert response.status_code == 200
    messages = [record.getMessage() for record in caplog.records]
    timing = next(message for message in messages if message.startswith("query_complete "))
    assert "mode=manual" in timing
    assert "forged" not in timing
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
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args, **_kwargs: [lot_row()])
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
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args, **_kwargs: [row])
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
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args, **_kwargs: [row])
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
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args, **_kwargs: [lot_row()])
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
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args, **_kwargs: [lot_row()])
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


def test_district_query_binds_real_fetch_current_lots_keywords(monkeypatch):
    """真實呼叫端必須把行政區綁到 district、新鮮度綁到 freshness_minutes。"""
    connection = SpyConnection([lot_row()])
    monkeypatch.setattr(app_module, "get_connection", lambda: connection)
    monkeypatch.setattr(app_module, "fetch_matching_history", lambda *_args: [])

    response = make_client().post("/api/query", json={
        "mode": "manual", "district": "信義區",
        "arrival_time": "2026-08-04T18:00:00+08:00",
    })

    sql, params = connection.spy_cursor.calls[0]
    assert "AND district = %s" in sql
    assert params == (45, "臺北市", "信義區")
    body = response.get_json()
    assert response.status_code == 200
    assert body["recommendations"][0]["lot_id"] == "TPE1"


def test_regular_query_does_not_preload_history(monkeypatch):
    """一般查詢只使用即時資料；歷史留給使用者點擊後的專用端點。"""
    connection = CloseTrackingConnection()
    monkeypatch.setattr(app_module, "get_connection", lambda: connection)
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args, **_kwargs: [lot_row()])
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
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args, **_kwargs: rows)

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

    def current_lots(connection, *_args, **_kwargs):
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
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args, **_kwargs: [row])
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
                        lambda *_args, **_kwargs: [lot_row(datetime(2026, 8, 3, 10))])
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
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("不應查停車場")),
    )

    response = make_client().post("/api/query", json={
        "mode": "manual", "address": "不存在的地址",
        "arrival_time": "2026-08-04T18:00:00+08:00",
    })

    assert response.status_code == 422
    body = response.get_json()
    UUID(body["request_id"])
    assert body["fallback"] == "district"
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
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args, **_kwargs: [lot_row()])
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
    body = response.get_json()
    UUID(body["request_id"])
    assert body["error"] == "失敗"
    assert body["fallback"] == "manual"
