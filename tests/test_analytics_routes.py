"""分析路由測試：查詢儀表、事件端點白名單與最佳努力寫入。"""

import hashlib
import hmac
from datetime import datetime, timezone
from uuid import UUID

import pytest

import app as app_module
from ai_service import LocationCandidate, ParkingIntent

VALID_UUID = "550e8400-e29b-41d4-a716-446655440000"
VALID_REQUEST_ID = "660e8400-e29b-41d4-a716-446655440001"


class CloseTrackingConnection:
    """記錄連線是否關閉，並提供 commit/rollback 假實作。"""

    def __init__(self):
        self.closed = False
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self

    def execute(self, *_args):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def make_analytics_app(monkeypatch, **config):
    """建立測試用 Flask 應用，並隔離 DB 與外部地址服務。"""
    settings = {
        "TESTING": True, "SECRET_KEY": "test", "AUTO_REFRESH_ENABLED": False,
        "OPENROUTESERVICE_API_KEY": "", "ANALYTICS_HMAC_SECRET": "test-secret",
    }
    settings.update(config)
    flask_app = app_module.create_app(settings)
    monkeypatch.setattr(
        app_module, "fetch_current_lots", lambda *_args: [lot_row()])
    monkeypatch.setattr(
        app_module, "fetch_matching_history", lambda *_args: [])
    monkeypatch.setattr(
        app_module, "geocode_address", lambda *_args: {
            "display_address": "臺北市政府", "latitude": 25.0375,
            "longitude": 121.5637,
        })
    return flask_app


def lot_row():
    """建立可通過真實推薦流程的單一場站。"""
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
        "captured_at": datetime(2026, 8, 23, 2, 0, tzinfo=timezone.utc),
    }


def manual_payload(address="臺北市政府"):
    """建立手動查詢 payload；預設地址會通過地標別名快取。"""
    return {
        "mode": "manual", "address": address,
        "arrival_time": "2026-08-23T18:00:00+08:00",
    }


def analytics_headers():
    """回傳明確同意的分析標頭。"""
    return {
        "X-Analytics-Consent": "1",
        "X-Analytics-Id": VALID_UUID,
        "X-Analytics-Source": "installed_pwa",
    }


def valid_navigation_payload(**overrides):
    """回傳導航事件的最小合法 payload。"""
    payload = {
        "event_type": "navigation_clicked",
        "analytics_id": VALID_UUID,
        "request_id": VALID_REQUEST_ID,
        "clicked_rank": 1,
        "parking_lot_id": "TPE0001",
        "walking_minutes": 6.5,
        "availability_bucket": "11_plus",
        "source": "installed_pwa",
    }
    payload.update(overrides)
    return payload


def test_query_without_consent_never_writes_analytics(monkeypatch):
    app = make_analytics_app(monkeypatch)
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    written = []
    app.extensions["analytics_writer"] = written.append
    response = app.test_client().post("/api/query", json=manual_payload())
    assert response.status_code == 200
    assert written == []


def test_query_without_secret_works_and_never_writes_analytics(monkeypatch):
    """未設定 HMAC 秘密時，即使送出同意標頭也不得寫入任何分析事件。"""
    app = make_analytics_app(monkeypatch, ANALYTICS_HMAC_SECRET="")
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    written = []
    app.extensions["analytics_writer"] = written.append

    response = app.test_client().post(
        "/api/query", json=manual_payload(), headers=analytics_headers())

    body = response.get_json()
    assert response.status_code == 200
    UUID(body["request_id"])
    assert body["recommendations"][0]["lot_id"] == "TPE1"
    assert written == []


def test_consented_success_returns_request_id_and_records_no_destination(monkeypatch):
    app = make_analytics_app(monkeypatch)
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    written = []
    app.extensions["analytics_writer"] = written.append
    response = app.test_client().post(
        "/api/query", json=manual_payload(address="臺北車站"),
        headers=analytics_headers(),
    )
    body = response.get_json()
    UUID(body["request_id"])
    assert written[0]["event_type"] == "query_completed"
    assert written[0]["outcome_code"] == "success"
    assert "address" not in written[0]
    assert "臺北車站" not in repr(written[0])


def test_malformed_coordinates_cannot_break_public_query(monkeypatch):
    """事件建構異常時只能捨棄事件，不得讓查詢回應失敗。"""
    app = make_analytics_app(monkeypatch)
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    written = []
    app.extensions["analytics_writer"] = written.append
    monkeypatch.setattr(
        app_module, "build_query_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("bad coordinates")))

    response = app.test_client().post(
        "/api/query", json=manual_payload(), headers=analytics_headers())

    body = response.get_json()
    assert response.status_code == 200
    UUID(body["request_id"])
    assert written == []


def test_validation_failure_records_request_id_and_failed_validation(monkeypatch):
    app = make_analytics_app(monkeypatch)
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    written = []
    app.extensions["analytics_writer"] = written.append
    response = app.test_client().post(
        "/api/query",
        json={"mode": "manual", "district": "板橋區",
              "arrival_time": "2026-08-23T18:00:00+08:00"},
        headers=analytics_headers(),
    )
    body = response.get_json()
    assert response.status_code == 400
    UUID(body["request_id"])
    assert written[0]["outcome_code"] == "failed_validation"


def test_geocode_miss_records_request_id_and_failed_geocode(monkeypatch):
    app = make_analytics_app(monkeypatch)
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    written = []
    app.extensions["analytics_writer"] = written.append
    monkeypatch.setattr(app_module, "geocode_address", lambda *_args: None)

    response = app.test_client().post(
        "/api/query", json=manual_payload(address="不存在的地址"),
        headers=analytics_headers(),
    )
    body = response.get_json()
    assert response.status_code == 422
    UUID(body["request_id"])
    assert body["fallback"] == "district"
    assert written[0]["outcome_code"] == "failed_geocode"


def test_gemini_failure_records_failed_internal_only_with_consent(monkeypatch):
    app = make_analytics_app(monkeypatch)
    written = []
    app.extensions["analytics_writer"] = written.append
    monkeypatch.setattr(
        app_module, "parse_parking_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            app_module.IntentServiceError("Gemini 尚未設定")),
    )

    consented = app.test_client().post(
        "/api/query", json={"mode": "chat", "message": "我要去市政府"},
        headers=analytics_headers())
    no_consent = app.test_client().post(
        "/api/query", json={"mode": "chat", "message": "我要去市政府"})

    body = consented.get_json()
    assert consented.status_code == 503
    assert body["fallback"] == "manual"
    UUID(body["request_id"])
    assert [event["outcome_code"] for event in written] == ["failed_internal"]
    assert no_consent.status_code == 503


def test_no_ranked_candidates_records_failed_no_candidates(monkeypatch):
    """沒有候選時維持舊的 200 空群組契約，但仍記錄失敗事件。"""
    app = make_analytics_app(monkeypatch)
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    written = []
    app.extensions["analytics_writer"] = written.append
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args: [])

    response = app.test_client().post(
        "/api/query", json=manual_payload(), headers=analytics_headers())

    body = response.get_json()
    assert response.status_code == 200
    UUID(body["request_id"])
    assert body["current"] == {"district_score": None, "valid_lot_count": 0}
    for group in ("recommendations", "nearest", "warning", "avoid"):
        assert body[group] == []
    assert written[0]["outcome_code"] == "failed_no_candidates"


def test_no_candidates_query_keeps_200_empty_groups_contract(monkeypatch):
    """空結果必須維持原始 200 成功形狀，只加上 request_id。"""
    app = make_analytics_app(monkeypatch)
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    monkeypatch.setattr(app_module, "fetch_current_lots", lambda *_args: [])

    response = app.test_client().post("/api/query", json=manual_payload())

    body = response.get_json()
    assert response.status_code == 200
    UUID(body["request_id"])
    assert body["current"] == {"district_score": None, "valid_lot_count": 0}
    for group in ("recommendations", "nearest", "warning", "avoid"):
        assert body[group] == []
    assert body["history"] == {
        "hell_score": None, "sample_count": 0, "comparison": None}
    assert body["data_status"] == "fresh"


def test_location_choice_response_creates_no_event(monkeypatch):
    """需要選址的階段性回應不得寫入完成或失敗事件。"""
    app = make_analytics_app(monkeypatch)
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    written = []
    app.extensions["analytics_writer"] = written.append
    monkeypatch.setattr(app_module, "parse_parking_query", lambda *_args: ParkingIntent(
        intent="recommend", original_destination="資策會", address="資策會",
        district="松山區",
        arrival_time=datetime(2026, 8, 23, 10, tzinfo=timezone.utc),
        missing_fields=[],
        location_candidates=[
            LocationCandidate(name="A", address="臺北市大安區信義路三段153號"),
            LocationCandidate(name="B", address="臺北市松山區民生東路四段133號"),
        ],
    ))
    monkeypatch.setattr(app_module, "geocode_candidates", lambda *_args: [
        {"name": "A", "address": "臺北市大安區信義路三段153號",
         "district": "大安區", "display_address": "信義路三段153號",
         "latitude": 25.03, "longitude": 121.54},
        {"name": "B", "address": "臺北市松山區民生東路四段133號",
         "district": "松山區", "display_address": "民生東路四段133號",
         "latitude": 25.06, "longitude": 121.55},
    ])

    response = app.test_client().post(
        "/api/query", json={"mode": "chat", "message": "我要去資策會"},
        headers={**analytics_headers(), "X-Client-Version": "2"})

    body = response.get_json()
    assert response.status_code == 200
    assert body["needs_location_choice"] is True
    UUID(body["request_id"])
    assert set(body) == {
        "needs_location_choice", "location_choices", "arrival_time",
        "intent", "request_id",
    }
    assert written == []


def test_location_choice_response_does_not_echo_request_payload(monkeypatch):
    """選址回應只能回傳固定欄位，不得外洩 mode/message 或未知鍵。"""
    app = make_analytics_app(monkeypatch)
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    written = []
    app.extensions["analytics_writer"] = written.append
    monkeypatch.setattr(app_module, "parse_parking_query", lambda *_args: ParkingIntent(
        intent="recommend", original_destination="資策會", address="資策會",
        district="松山區",
        arrival_time=datetime(2026, 8, 23, 10, tzinfo=timezone.utc),
        missing_fields=[],
        location_candidates=[
            LocationCandidate(name="A", address="臺北市大安區信義路三段153號"),
            LocationCandidate(name="B", address="臺北市松山區民生東路四段133號"),
        ],
    ))
    monkeypatch.setattr(app_module, "geocode_candidates", lambda *_args: [
        {"name": "A", "address": "臺北市大安區信義路三段153號",
         "district": "大安區", "display_address": "信義路三段153號",
         "latitude": 25.03, "longitude": 121.54},
        {"name": "B", "address": "臺北市松山區民生東路四段133號",
         "district": "松山區", "display_address": "民生東路四段133號",
         "latitude": 25.06, "longitude": 121.55},
    ])

    response = app.test_client().post(
        "/api/query",
        json={"mode": "chat", "message": "我要去資策會", "client_hint": "secret"},
        headers={**analytics_headers(), "X-Client-Version": "2"})

    body = response.get_json()
    assert response.status_code == 200
    assert set(body) == {
        "needs_location_choice", "location_choices", "arrival_time",
        "intent", "request_id",
    }
    assert "client_hint" not in body
    assert "message" not in body


def test_stale_client_version_records_failed_validation_when_consented(monkeypatch):
    """舊版客戶端收到 409 時，同意下仍要記錄 failed_validation 事件。"""
    app = make_analytics_app(monkeypatch)
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    written = []
    app.extensions["analytics_writer"] = written.append
    monkeypatch.setattr(app_module, "parse_parking_query", lambda *_args: ParkingIntent(
        intent="recommend", original_destination="資策會", address="資策會",
        district="松山區",
        arrival_time=datetime(2026, 8, 23, 10, tzinfo=timezone.utc),
        missing_fields=[],
        location_candidates=[
            LocationCandidate(name="A", address="臺北市大安區信義路三段153號"),
        ],
    ))
    monkeypatch.setattr(app_module, "geocode_candidates", lambda *_args: [{
        "name": "A", "address": "臺北市大安區信義路三段153號",
        "district": "大安區", "display_address": "信義路三段153號",
        "latitude": 25.03, "longitude": 121.54,
    }])

    response = app.test_client().post(
        "/api/query", json={"mode": "chat", "message": "我要去資策會"},
        headers=analytics_headers())

    body = response.get_json()
    assert response.status_code == 409
    UUID(body["request_id"])
    assert written[0]["outcome_code"] == "failed_validation"


def test_navigation_event_requires_body_uuid_and_allowlisted_fields(monkeypatch):
    """事件端點只接受 body 的合法 UUID 與白名單欄位，不依賴自訂標頭。"""
    app = make_analytics_app(monkeypatch)
    client = app.test_client()
    forbidden = [
        {"analytics_id": None},
        {"request_id": "not-a-uuid"},
        {"address": "不得接受"},
        {"request_id": None},
    ]
    for overrides in forbidden:
        response = client.post(
            "/api/analytics/events",
            json=valid_navigation_payload(**overrides),
        )
        assert response.status_code == 400
    assert client.post(
        "/api/analytics/events",
        json=valid_navigation_payload(),
    ).status_code == 204


def test_navigation_event_writes_hashed_id_and_returns_204(monkeypatch):
    app = make_analytics_app(monkeypatch)
    written = []
    app.extensions["analytics_writer"] = written.append
    response = app.test_client().post(
        "/api/analytics/events",
        json=valid_navigation_payload(),
    )
    assert response.status_code == 204
    event = written[0]
    assert event["event_type"] == "navigation_clicked"
    assert event["request_id"] == VALID_REQUEST_ID
    assert len(event["anonymous_id_hash"]) == 64
    assert VALID_UUID not in event["anonymous_id_hash"]
    assert event["clicked_rank"] == 1
    assert event["parking_lot_id"] == "TPE0001"
    assert event["walking_minutes"] == 6.5
    assert event["availability_bucket"] == "11_plus"


def test_navigation_event_accepts_rank_zero_but_rejects_negative(monkeypatch):
    """其他場站的精簡導航必須能送 rank 0；負值仍要拒絕。"""
    app = make_analytics_app(monkeypatch)
    written = []
    app.extensions["analytics_writer"] = written.append
    client = app.test_client()

    accepted = client.post(
        "/api/analytics/events",
        json=valid_navigation_payload(clicked_rank=0),
    )
    rejected = client.post(
        "/api/analytics/events",
        json=valid_navigation_payload(clicked_rank=-1),
    )

    assert accepted.status_code == 204
    assert written[0]["clicked_rank"] == 0
    assert rejected.status_code == 400


def test_navigation_event_rejects_parking_lot_id_over_32_chars(monkeypatch):
    """parking_lot_id 上限必須與資料表 VARCHAR(32) 一致。"""
    app = make_analytics_app(monkeypatch)
    client = app.test_client()

    assert client.post(
        "/api/analytics/events",
        json=valid_navigation_payload(parking_lot_id="L" * 33),
    ).status_code == 400
    assert client.post(
        "/api/analytics/events",
        json=valid_navigation_payload(parking_lot_id="L" * 32),
    ).status_code == 204


def test_event_hmac_derives_from_body_uuid_not_headers(monkeypatch):
    """HMAC 必須由 body 的 analytics_id 計算，即使標頭帶其他 UUID 也不受影響。"""
    app = make_analytics_app(monkeypatch)
    written = []
    app.extensions["analytics_writer"] = written.append
    response = app.test_client().post(
        "/api/analytics/events",
        json=valid_navigation_payload(),
        headers={"X-Analytics-Id": "11111111-1111-1111-1111-111111111111"},
    )
    assert response.status_code == 204
    expected_hash = hmac.new(
        "test-secret".encode(), VALID_UUID.encode(), hashlib.sha256).hexdigest()
    assert written[0]["anonymous_id_hash"] == expected_hash


def test_pwa_opened_event_writes_without_request_id(monkeypatch):
    app = make_analytics_app(monkeypatch)
    written = []
    app.extensions["analytics_writer"] = written.append
    response = app.test_client().post(
        "/api/analytics/events",
        json={
            "event_type": "pwa_opened",
            "analytics_id": VALID_UUID,
            "source": "installed_pwa",
        },
    )
    assert response.status_code == 204
    assert written[0]["event_type"] == "pwa_opened"
    assert written[0]["request_id"] is None


def test_disabled_analytics_returns_204_without_writing(monkeypatch):
    app = make_analytics_app(
        monkeypatch, ANALYTICS_HMAC_SECRET="", ANALYTICS_ENABLED=False)
    written = []
    app.extensions["analytics_writer"] = written.append
    response = app.test_client().post(
        "/api/analytics/events",
        json=valid_navigation_payload(),
    )
    assert response.status_code == 204
    assert written == []


def test_event_endpoint_survives_writer_exception(monkeypatch, caplog):
    app = make_analytics_app(monkeypatch)
    app.extensions["analytics_writer"] = lambda _event: (_ for _ in ()).throw(
        RuntimeError("db down"))
    with caplog.at_level("WARNING"):
        response = app.test_client().post(
            "/api/analytics/events",
            json=valid_navigation_payload(),
        )
    assert response.status_code == 204
    assert "analytics_write_failed" in caplog.text


def test_query_survives_writer_exception(monkeypatch, caplog):
    app = make_analytics_app(monkeypatch)
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    app.extensions["analytics_writer"] = lambda _event: (_ for _ in ()).throw(
        RuntimeError("db down"))
    with caplog.at_level("WARNING"):
        response = app.test_client().post(
            "/api/query", json=manual_payload(), headers=analytics_headers())
    assert response.status_code == 200
    assert response.get_json()["recommendations"][0]["lot_id"] == "TPE1"
    failures = [
        record.getMessage() for record in caplog.records
        if "analytics_write_failed" in record.getMessage()
    ]
    assert failures == ["analytics_write_failed event=query_completed"]
    assert "臺北市政府" not in caplog.text


def test_terminal_events_use_elapsed_helper_for_duration(monkeypatch):
    """所有終端事件（成功與各失敗路徑）都經由 elapsed_ms 記錄實際耗時。"""
    app = make_analytics_app(monkeypatch)
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    monkeypatch.setattr(app_module, "elapsed_ms", lambda _started, now=None: 1234)
    written = []
    app.extensions["analytics_writer"] = written.append

    success = app.test_client().post(
        "/api/query", json=manual_payload(), headers=analytics_headers())
    validation = app.test_client().post(
        "/api/query",
        json={"mode": "manual", "district": "板橋區",
              "arrival_time": "2026-08-23T18:00:00+08:00"},
        headers=analytics_headers())
    monkeypatch.setattr(app_module, "geocode_address", lambda *_args: None)
    geocode = app.test_client().post(
        "/api/query", json=manual_payload(address="不存在的地址"),
        headers=analytics_headers())
    monkeypatch.setattr(
        app_module, "parse_parking_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            app_module.IntentServiceError("Gemini 尚未設定")),
    )
    gemini = app.test_client().post(
        "/api/query", json={"mode": "chat", "message": "我要去市政府"},
        headers=analytics_headers())
    monkeypatch.setattr(
        app_module, "geocode_address", lambda *_args: {
            "display_address": "臺北市政府", "latitude": 25.0375,
            "longitude": 121.5637,
        })
    monkeypatch.setattr(
        app_module, "fetch_current_lots",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    internal = app.test_client().post(
        "/api/query", json=manual_payload(), headers=analytics_headers())

    assert success.status_code == 200
    assert validation.status_code == 400
    assert geocode.status_code == 422
    assert gemini.status_code == 503
    assert internal.status_code == 503
    assert [event["duration_ms"] for event in written] == [1234] * 5
    assert [event["outcome_code"] for event in written] == [
        "success", "failed_validation", "failed_geocode",
        "failed_internal", "failed_internal",
    ]


def test_write_analytics_safely_tolerates_missing_event_type(monkeypatch, caplog):
    """缺少 event_type 的畸形事件只能留下安全警告，不能讓端點失敗。"""
    app = make_analytics_app(monkeypatch)
    app.extensions["analytics_writer"] = lambda _event: (_ for _ in ()).throw(
        RuntimeError("db down"))
    monkeypatch.setattr(
        app_module, "build_browser_event", lambda **_kwargs: {
            "anonymous_id_hash": "a" * 64,
        })
    with caplog.at_level("WARNING"):
        response = app.test_client().post(
            "/api/analytics/events",
            json=valid_navigation_payload(),
        )
    assert response.status_code == 204
    failures = [
        record.getMessage() for record in caplog.records
        if "analytics_write_failed" in record.getMessage()
    ]
    assert failures == ["analytics_write_failed event=unknown"]
    assert "不得接受" not in caplog.text


def test_production_writer_commits_and_closes_fresh_connection(monkeypatch):
    connection = CloseTrackingConnection()
    monkeypatch.setattr(app_module, "get_connection", lambda: connection)
    called = []

    def insert_event(_connection, event):
        called.append(event["event_type"])
        return 1

    monkeypatch.setattr(app_module, "insert_event", insert_event)
    app = make_analytics_app(monkeypatch)
    writer = app.extensions["analytics_writer"]

    writer({
        "event_type": "query_completed",
        "anonymous_id_hash": "a" * 64,
    })

    assert called == ["query_completed"]
    assert connection.committed is True
    assert connection.closed is True


def test_production_writer_rolls_back_and_closes_on_error(monkeypatch):
    connection = CloseTrackingConnection()
    monkeypatch.setattr(app_module, "get_connection", lambda: connection)

    def insert_event(_connection, _event):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(app_module, "insert_event", insert_event)
    app = make_analytics_app(monkeypatch)
    writer = app.extensions["analytics_writer"]

    with pytest.raises(RuntimeError, match="insert failed"):
        writer({"event_type": "query_completed", "anonymous_id_hash": "a" * 64})

    assert connection.rolled_back is True
    assert connection.closed is True


def test_production_writer_routes_navigation_events(monkeypatch):
    connection = CloseTrackingConnection()
    monkeypatch.setattr(app_module, "get_connection", lambda: connection)
    called = []

    def insert_navigation_event(_connection, _event):
        called.append(True)
        return 1

    monkeypatch.setattr(app_module, "insert_navigation_event",
                        insert_navigation_event)
    app = make_analytics_app(monkeypatch)
    writer = app.extensions["analytics_writer"]

    writer({
        "event_type": "navigation_clicked",
        "anonymous_id_hash": "a" * 64,
    })

    assert called == [True]
    assert connection.committed is True
    assert connection.closed is True


def test_address_query_records_inferred_district_timings_and_three_snapshots(monkeypatch):
    """地址查詢要記錄推導行政區、分段耗時與最多三筆推薦快照。"""
    app = make_analytics_app(monkeypatch)
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    captured = {"details": [], "recommendations": []}
    app.extensions["analytics_detail_writer"] = captured["details"].append
    app.extensions["analytics_recommendation_writer"] = \
        captured["recommendations"].append

    response = app.test_client().post(
        "/api/query", json=manual_payload("台北車站"), headers=analytics_headers())

    assert response.status_code == 200
    detail = captured["details"][0]
    assert detail["district"] == "中正區"
    assert detail["total_ms"] >= 0
    assert detail["parse_ms"] is not None
    assert detail["geocode_ms"] is not None
    assert len(captured["recommendations"][0]) <= 3


def test_analytics_detail_failure_never_changes_success_response(monkeypatch, caplog):
    """查詢明細寫入失敗時，公開查詢仍要回傳 200 與完整推薦。"""
    app = make_analytics_app(monkeypatch)
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    captured = {"recommendations": []}
    app.extensions["analytics_detail_writer"] = (
        lambda _row: (_ for _ in ()).throw(RuntimeError("down")))
    app.extensions["analytics_recommendation_writer"] = \
        captured["recommendations"].append

    with caplog.at_level("WARNING"):
        response = app.test_client().post(
            "/api/query", json=manual_payload(), headers=analytics_headers())

    body = response.get_json()
    assert response.status_code == 200
    assert body["recommendations"]
    assert len(captured["recommendations"][0]) <= 3
    assert "analytics_detail_write_failed request_id=" in caplog.text


def test_analytics_snapshot_failure_never_changes_success_response(monkeypatch, caplog):
    """推薦快照寫入失敗時，公開查詢仍要回傳 200 與完整推薦。"""
    app = make_analytics_app(monkeypatch)
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    captured = {"details": []}
    app.extensions["analytics_detail_writer"] = captured["details"].append
    app.extensions["analytics_recommendation_writer"] = (
        lambda _rows: (_ for _ in ()).throw(RuntimeError("down")))

    with caplog.at_level("WARNING"):
        response = app.test_client().post(
            "/api/query", json=manual_payload(), headers=analytics_headers())

    assert response.status_code == 200
    assert response.get_json()["recommendations"]
    assert captured["details"][0]["outcome_code"] == "success"
    assert "analytics_recommendation_write_failed request_id=" in caplog.text


def test_query_without_consent_records_no_details_or_snapshots(monkeypatch):
    """未同意時，查詢明細與推薦快照都不能寫入。"""
    app = make_analytics_app(monkeypatch)
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    captured = {"details": [], "recommendations": []}
    app.extensions["analytics_detail_writer"] = captured["details"].append
    app.extensions["analytics_recommendation_writer"] = \
        captured["recommendations"].append

    response = app.test_client().post("/api/query", json=manual_payload())

    assert response.status_code == 200
    assert captured["details"] == []
    assert captured["recommendations"] == []


def test_geocode_failure_records_detail_error_stage(monkeypatch):
    """地理編碼失敗時，明細要記錄 error_stage=geocode。"""
    app = make_analytics_app(monkeypatch)
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    monkeypatch.setattr(app_module, "geocode_address", lambda *_args: None)
    captured = {"details": []}
    app.extensions["analytics_detail_writer"] = captured["details"].append

    response = app.test_client().post(
        "/api/query", json=manual_payload(address="不存在的地址"),
        headers=analytics_headers())

    assert response.status_code == 422
    detail = captured["details"][0]
    assert detail["error_stage"] == "geocode"
    assert detail["outcome_code"] == "failed_geocode"


def test_location_choice_records_detail_without_query_event(monkeypatch):
    """選址回應維持 200，只寫 location_choice_required 明細，不寫查詢事件。"""
    app = make_analytics_app(monkeypatch)
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    captured = {"details": [], "events": []}
    app.extensions["analytics_detail_writer"] = captured["details"].append
    app.extensions["analytics_writer"] = captured["events"].append
    monkeypatch.setattr(app_module, "parse_parking_query", lambda *_args: ParkingIntent(
        intent="recommend", original_destination="資策會", address="資策會",
        district="松山區",
        arrival_time=datetime(2026, 8, 23, 10, tzinfo=timezone.utc),
        missing_fields=[],
        location_candidates=[
            LocationCandidate(name="A", address="臺北市大安區信義路三段153號"),
            LocationCandidate(name="B", address="臺北市松山區民生東路四段133號"),
        ],
    ))
    monkeypatch.setattr(app_module, "geocode_candidates", lambda *_args: [
        {"name": "A", "address": "臺北市大安區信義路三段153號",
         "district": "大安區", "display_address": "信義路三段153號",
         "latitude": 25.03, "longitude": 121.54},
        {"name": "B", "address": "臺北市松山區民生東路四段133號",
         "district": "松山區", "display_address": "民生東路四段133號",
         "latitude": 25.06, "longitude": 121.55},
    ])

    response = app.test_client().post(
        "/api/query", json={"mode": "chat", "message": "我要去資策會"},
        headers={**analytics_headers(), "X-Client-Version": "2"})

    assert response.status_code == 200
    assert captured["events"] == []
    detail = captured["details"][0]
    assert detail["outcome_code"] == "location_choice_required"
    assert detail["location_choice_count"] == 2


def test_location_choice_survives_detail_writer_failure(monkeypatch, caplog):
    """選址明細寫入失敗時，選址回應仍必須維持 HTTP 200。"""
    app = make_analytics_app(monkeypatch)
    monkeypatch.setattr(app_module, "get_connection", CloseTrackingConnection)
    app.extensions["analytics_detail_writer"] = (
        lambda _row: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(app_module, "parse_parking_query", lambda *_args: ParkingIntent(
        intent="recommend", original_destination="資策會", address="資策會",
        district="松山區",
        arrival_time=datetime(2026, 8, 23, 10, tzinfo=timezone.utc),
        missing_fields=[],
        location_candidates=[
            LocationCandidate(name="A", address="臺北市大安區信義路三段153號"),
            LocationCandidate(name="B", address="臺北市松山區民生東路四段133號"),
        ],
    ))
    monkeypatch.setattr(app_module, "geocode_candidates", lambda *_args: [
        {"name": "A", "address": "臺北市大安區信義路三段153號",
         "district": "大安區", "display_address": "信義路三段153號",
         "latitude": 25.03, "longitude": 121.54},
        {"name": "B", "address": "臺北市松山區民生東路四段133號",
         "district": "松山區", "display_address": "民生東路四段133號",
         "latitude": 25.06, "longitude": 121.55},
    ])

    with caplog.at_level("WARNING"):
        response = app.test_client().post(
            "/api/query", json={"mode": "chat", "message": "我要去資策會"},
            headers={**analytics_headers(), "X-Client-Version": "2"})

    assert response.status_code == 200
    assert response.get_json()["needs_location_choice"] is True
    assert "analytics_detail_write_failed request_id=" in caplog.text


def test_production_detail_writer_commits_and_closes_fresh_connection(monkeypatch):
    connection = CloseTrackingConnection()
    monkeypatch.setattr(app_module, "get_connection", lambda: connection)
    called = []

    def upsert_query_detail(_connection, detail):
        called.append(detail)
        return 1

    monkeypatch.setattr(app_module, "upsert_query_detail", upsert_query_detail)
    app = make_analytics_app(monkeypatch)
    writer = app.extensions["analytics_detail_writer"]

    writer({"request_id": VALID_REQUEST_ID})

    assert called == [{"request_id": VALID_REQUEST_ID}]
    assert connection.committed is True
    assert connection.closed is True


def test_production_detail_writer_rolls_back_and_closes_on_error(monkeypatch):
    connection = CloseTrackingConnection()
    monkeypatch.setattr(app_module, "get_connection", lambda: connection)

    def upsert_query_detail(_connection, _detail):
        raise RuntimeError("upsert failed")

    monkeypatch.setattr(app_module, "upsert_query_detail", upsert_query_detail)
    app = make_analytics_app(monkeypatch)
    writer = app.extensions["analytics_detail_writer"]

    with pytest.raises(RuntimeError, match="upsert failed"):
        writer({"request_id": VALID_REQUEST_ID})

    assert connection.rolled_back is True
    assert connection.closed is True


def test_production_snapshot_writer_replaces_commits_and_closes(monkeypatch):
    connection = CloseTrackingConnection()
    monkeypatch.setattr(app_module, "get_connection", lambda: connection)
    called = []

    def replace_recommendation_snapshots(_connection, request_id, rows):
        called.append((request_id, rows))
        return 3

    monkeypatch.setattr(app_module, "replace_recommendation_snapshots",
                        replace_recommendation_snapshots)
    app = make_analytics_app(monkeypatch)
    writer = app.extensions["analytics_recommendation_writer"]
    rows = [{"request_id": VALID_REQUEST_ID, "rank_position": 1}]

    writer(rows)

    assert called == [(VALID_REQUEST_ID, rows)]
    assert connection.committed is True
    assert connection.closed is True


def test_production_snapshot_writer_rolls_back_and_closes_on_error(monkeypatch):
    connection = CloseTrackingConnection()
    monkeypatch.setattr(app_module, "get_connection", lambda: connection)

    def replace_recommendation_snapshots(_connection, _request_id, _rows):
        raise RuntimeError("replace failed")

    monkeypatch.setattr(app_module, "replace_recommendation_snapshots",
                        replace_recommendation_snapshots)
    app = make_analytics_app(monkeypatch)
    writer = app.extensions["analytics_recommendation_writer"]

    with pytest.raises(RuntimeError, match="replace failed"):
        writer([{"request_id": VALID_REQUEST_ID, "rank_position": 1}])

    assert connection.rolled_back is True
    assert connection.closed is True


@pytest.mark.parametrize("event_type", [
    "location_choice_shown", "location_choice_selected",
    "map_marker_clicked", "history_opened",
])
def test_new_browser_events_are_accepted(monkeypatch, event_type):
    app = make_analytics_app(monkeypatch)
    app.extensions["analytics_writer"] = lambda _event: None
    response = app.test_client().post("/api/analytics/events", json={
        "event_type": event_type, "analytics_id": VALID_UUID,
        "request_id": VALID_REQUEST_ID, "source": "direct",
        "clicked_rank": 1, "parking_lot_id": "TPE1",
    })
    assert response.status_code == 204


def test_new_browser_event_writes_hashed_id_and_scalars(monkeypatch):
    """新事件同樣以 body UUID 計算 HMAC，並保留純量欄位。"""
    app = make_analytics_app(monkeypatch)
    written = []
    app.extensions["analytics_writer"] = written.append
    response = app.test_client().post("/api/analytics/events", json={
        "event_type": "history_opened", "analytics_id": VALID_UUID,
        "request_id": VALID_REQUEST_ID, "source": "direct",
        "parking_lot_id": "TPE1",
    })
    assert response.status_code == 204
    event = written[0]
    assert event["event_type"] == "history_opened"
    assert event["request_id"] == VALID_REQUEST_ID
    assert event["parking_lot_id"] == "TPE1"
    assert len(event["anonymous_id_hash"]) == 64


def test_event_endpoint_rejects_unknown_event_type(monkeypatch):
    """事件端點只能接受固定白名單，其他類型一律 400。"""
    app = make_analytics_app(monkeypatch)
    response = app.test_client().post(
        "/api/analytics/events",
        json=valid_navigation_payload(event_type="screen_view"),
    )
    assert response.status_code == 400


def test_feedback_updates_only_matching_request_and_uuid(monkeypatch):
    app = make_analytics_app(monkeypatch)
    captured = []
    app.extensions["analytics_feedback_writer"] = \
        lambda *args: captured.append(args) or 1
    response = app.test_client().post("/api/analytics/feedback", json={
        "analytics_id": VALID_UUID, "request_id": VALID_REQUEST_ID,
        "feedback_code": "found_space",
    })
    assert response.status_code == 204
    assert captured[0][1:] == (VALID_REQUEST_ID, "found_space")


def test_feedback_rejects_unknown_codes_and_fields(monkeypatch):
    app = make_analytics_app(monkeypatch)
    app.extensions["analytics_feedback_writer"] = lambda *args: 1
    client = app.test_client()
    payload = {
        "analytics_id": VALID_UUID, "request_id": VALID_REQUEST_ID,
        "feedback_code": "found_space",
    }
    for overrides in ({"feedback_code": "loved_it"},
                      {"client_hint": "secret"}):
        response = client.post(
            "/api/analytics/feedback", json={**payload, **overrides})
        assert response.status_code == 400
    assert client.post(
        "/api/analytics/feedback", json=payload).status_code == 204


def test_feedback_returns_404_when_no_matching_detail(monkeypatch):
    """沒有對應 request＋裝置的明細時，回饋必須回 404 而非靜默成功。"""
    app = make_analytics_app(monkeypatch)
    app.extensions["analytics_feedback_writer"] = lambda *args: 0
    response = app.test_client().post("/api/analytics/feedback", json={
        "analytics_id": VALID_UUID, "request_id": VALID_REQUEST_ID,
        "feedback_code": "full_on_arrival",
    })
    assert response.status_code == 404


def test_feedback_identical_repeat_returns_204_when_detail_exists(monkeypatch):
    """同 request＋hash 重複送出相同回饋時，即使 UPDATE 0 列也必須回 204。"""
    app = make_analytics_app(monkeypatch)
    calls = []
    app.extensions["analytics_feedback_writer"] = (
        lambda *args: calls.append(args) or 1)
    client = app.test_client()
    payload = {
        "analytics_id": VALID_UUID, "request_id": VALID_REQUEST_ID,
        "feedback_code": "found_space",
    }
    assert client.post("/api/analytics/feedback", json=payload).status_code == 204
    assert client.post("/api/analytics/feedback", json=payload).status_code == 204
    assert len(calls) == 2


def test_feedback_survives_writer_exception(monkeypatch, caplog):
    app = make_analytics_app(monkeypatch)
    app.extensions["analytics_feedback_writer"] = (
        lambda *args: (_ for _ in ()).throw(RuntimeError("db down")))
    with caplog.at_level("WARNING"):
        response = app.test_client().post("/api/analytics/feedback", json={
            "analytics_id": VALID_UUID, "request_id": VALID_REQUEST_ID,
            "feedback_code": "did_not_go",
        })
    assert response.status_code == 204
    assert "analytics_feedback_write_failed" in caplog.text


def test_feedback_disabled_analytics_returns_204(monkeypatch):
    app = make_analytics_app(
        monkeypatch, ANALYTICS_HMAC_SECRET="", ANALYTICS_ENABLED=False)
    written = []
    app.extensions["analytics_feedback_writer"] = written.append
    response = app.test_client().post("/api/analytics/feedback", json={
        "analytics_id": VALID_UUID, "request_id": VALID_REQUEST_ID,
        "feedback_code": "found_space",
    })
    assert response.status_code == 204
    assert written == []


def test_production_feedback_writer_commits_and_closes_fresh_connection(
        monkeypatch):
    connection = CloseTrackingConnection()
    monkeypatch.setattr(app_module, "get_connection", lambda: connection)
    called = []

    def update_query_feedback(_connection, request_id, anonymous_id_hash,
                              feedback_code):
        called.append((request_id, anonymous_id_hash, feedback_code))
        return 1

    monkeypatch.setattr(app_module, "update_query_feedback",
                        update_query_feedback)
    app = make_analytics_app(monkeypatch)
    writer = app.extensions["analytics_feedback_writer"]

    result = writer("b" * 64, VALID_REQUEST_ID, "found_space")

    assert result == 1
    assert called == [(VALID_REQUEST_ID, "b" * 64, "found_space")]
    assert connection.committed is True
    assert connection.closed is True


def test_production_feedback_writer_rolls_back_and_closes_on_error(monkeypatch):
    connection = CloseTrackingConnection()
    monkeypatch.setattr(app_module, "get_connection", lambda: connection)

    def update_query_feedback(_connection, _request_id, _hash, _code):
        raise RuntimeError("update failed")

    monkeypatch.setattr(app_module, "update_query_feedback",
                        update_query_feedback)
    app = make_analytics_app(monkeypatch)
    writer = app.extensions["analytics_feedback_writer"]

    with pytest.raises(RuntimeError, match="update failed"):
        writer("b" * 64, VALID_REQUEST_ID, "found_space")

    assert connection.rolled_back is True
    assert connection.closed is True


def test_run_analytics_write_preserves_connection_failure(monkeypatch):
    """get_connection 失敗時必須拋出根因，不能用 NameError 蓋掉。"""
    def raise_connection():
        raise RuntimeError("connection down")

    monkeypatch.setattr(app_module, "get_connection", raise_connection)
    app = make_analytics_app(monkeypatch)
    with pytest.raises(RuntimeError, match="connection down"):
        app.extensions["analytics_detail_writer"]({})
