"""管理儀表板路由測試：唯讀、no-store、範圍驗證與狀態降級。"""

from datetime import datetime, timedelta, timezone

import app as app_module
import status_service

NOW_UTC = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)
STATUS_TIMES = [{
    "official_data_at": datetime(2026, 8, 23, 7, 15, tzinfo=timezone.utc),
    "collector_at": datetime(2026, 8, 23, 7, 45, tzinfo=timezone.utc),
    "metadata_at": datetime(2026, 8, 23, 7, 50, tzinfo=timezone.utc),
}]
FIXED_SYSTEM = {
    "memory_percent": 75.0, "load_5m": 0.3, "disk_remaining_percent": 40.0,
}


class FixedNow(datetime):
    """把路由內的 datetime.now 固定成可預測的 UTC 時間。"""

    @classmethod
    def now(cls, tz=None):
        return NOW_UTC if tz is None else NOW_UTC.astimezone(tz)


class FakeCursor:
    """記錄 SQL；依 SQL 內容回傳對應結果，資料庫錯誤時 execute 失敗。"""

    def __init__(self, connection):
        self.connection = connection
        self.last_sql = None

    def execute(self, sql, params=None):
        if self.connection.database_error:
            raise RuntimeError("database down")
        self.connection.executions.append((sql, params))
        self.last_sql = sql

    def fetchall(self):
        if self.last_sql and "SELECT 1" in self.last_sql:
            return [{"1": 1}]
        if self.last_sql and (
                "FROM analytics_query_details" in self.last_sql
                or "FROM analytics_recommendations" in self.last_sql):
            if not self.connection.analytics_queues:
                return []
            return self.connection.analytics_queues.pop(0)
        if self.last_sql and "official_data_at" in self.last_sql:
            return self.connection.status_times
        if not self.connection.analytics_queues:
            return []
        return self.connection.analytics_queues.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeConnection:
    """依 SQL 類別吐出結果列，並記錄 execute 與關閉狀態。"""

    def __init__(self, database_error=False, status_times=None,
                 analytics_queues=None):
        self.database_error = database_error
        self.status_times = status_times if status_times is not None \
            else STATUS_TIMES
        self.analytics_queues = list(analytics_queues or [[], [], []])
        self.closed = False
        self.executions = []

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


def query_row(event_type, request_id, device, outcome_code, duration_ms,
              result_count, occurred_at, district="中正區"):
    """建構 fetch_events 回傳的 16 鍵查詢事件列。"""
    return {
        "event_type": event_type,
        "occurred_at": occurred_at,
        "request_id": request_id,
        "anonymous_id_hash": device,
        "district": district,
        "area_bucket": None,
        "place_type": "station",
        "query_mode": "manual",
        "outcome_code": outcome_code,
        "duration_ms": duration_ms,
        "result_count": result_count,
        "clicked_rank": None,
        "parking_lot_id": None,
        "walking_minutes": None,
        "availability_bucket": None,
        "source": "direct",
    }


def nav_row(request_id, device, clicked_rank, occurred_at):
    """建構 fetch_events 回傳的 16 鍵導航點擊列。"""
    return {
        "event_type": "navigation_clicked",
        "occurred_at": occurred_at,
        "request_id": request_id,
        "anonymous_id_hash": device,
        "district": None,
        "area_bucket": None,
        "place_type": None,
        "query_mode": None,
        "outcome_code": None,
        "duration_ms": None,
        "result_count": None,
        "clicked_rank": clicked_rank,
        "parking_lot_id": "TPE0001",
        "walking_minutes": 6.5,
        "availability_bucket": "11_plus",
        "source": "direct",
    }


def make_admin_client(monkeypatch, database_error=False,
                      analytics_queues=None, no_secret=False,
                      segment_min_devices=None):
    """建立測試 Flask 用戶端，並隔離資料庫與本機資源讀取。"""
    settings = {
        "TESTING": True, "SECRET_KEY": "test", "DEPLOY_VERSION": "abc1234",
        "ANALYTICS_HMAC_SECRET": "" if no_secret else "test-secret",
    }
    if segment_min_devices is not None:
        settings["ANALYTICS_SEGMENT_MIN_DEVICES"] = segment_min_devices
    flask_app = app_module.create_app(settings)
    connections = []

    def fake_connection():
        connection = FakeConnection(
            database_error=database_error,
            analytics_queues=analytics_queues,
        )
        connections.append(connection)
        return connection

    monkeypatch.setattr(app_module, "datetime", FixedNow)
    monkeypatch.setattr(status_service, "datetime", FixedNow)
    monkeypatch.setattr(app_module, "get_connection", fake_connection)
    monkeypatch.setattr(
        status_service, "read_linux_status", lambda **_: dict(FIXED_SYSTEM))
    client = flask_app.test_client()
    client.connections = connections
    return client


def test_admin_pages_are_read_only_and_no_store(monkeypatch):
    client = make_admin_client(monkeypatch)
    page = client.get("/admin/analytics")
    data = client.get("/admin/api/analytics?range=7d")
    assert page.status_code == 200
    assert data.status_code == 200
    assert page.headers["Cache-Control"] == "no-store"
    assert page.headers["X-Robots-Tag"] == "noindex"
    assert data.headers["Cache-Control"] == "no-store"
    assert data.headers["X-Robots-Tag"] == "noindex"
    assert client.post("/admin/api/status").status_code == 405


def test_status_api_degrades_each_component_independently(monkeypatch):
    body = make_admin_client(monkeypatch, database_error=True).get(
        "/admin/api/status").get_json()
    assert body["application"]["tone"] == "green"
    assert body["database"]["tone"] == "red"
    assert body["official_data"]["tone"] == "gray"
    assert body["collector"]["tone"] == "gray"
    assert body["metadata"]["tone"] == "gray"


def test_status_api_reports_healthy_system_and_data(monkeypatch):
    body = make_admin_client(monkeypatch).get("/admin/api/status").get_json()
    assert body["application"]["tone"] == "green"
    assert body["database"]["tone"] == "green"
    assert body["official_data"]["tone"] == "yellow"  # 45 分鐘
    assert body["collector"]["tone"] == "green"  # 15 分鐘
    assert body["metadata"]["tone"] == "green"  # 10 分鐘
    assert body["load"]["tone"] == "yellow"  # 0.30
    assert body["memory"]["tone"] == "green"  # 75%
    assert body["disk"]["tone"] == "green"  # 40%
    assert body["deploy"]["tone"] == "green"
    assert body["analytics"]["tone"] == "green"


def test_status_api_handles_database_connection_failure(monkeypatch):
    app = app_module.create_app({"TESTING": True, "SECRET_KEY": "test"})
    monkeypatch.setattr(
        app_module, "get_connection",
        lambda: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    monkeypatch.setattr(
        status_service, "read_linux_status", lambda **_: dict(FIXED_SYSTEM))

    body = app.test_client().get("/admin/api/status").get_json()

    assert body["application"]["tone"] == "green"
    assert body["database"]["tone"] == "red"
    assert body["official_data"]["tone"] == "gray"
    assert body["load"]["tone"] == "yellow"


def test_analytics_api_rejects_unknown_range(monkeypatch):
    response = make_admin_client(monkeypatch).get(
        "/admin/api/analytics?range=90d")
    assert response.status_code == 400


def test_analytics_api_honestly_empty_without_secret(monkeypatch):
    client = make_admin_client(monkeypatch, no_secret=True)
    body = client.get("/admin/api/analytics?range=today").get_json()
    assert body["analytics_enabled"] is False
    assert body["summary"]["completed_queries"] == 0
    assert body["summary"]["query_success_rate"] is None
    assert client.get("/admin/api/status").get_json()[
        "analytics"]["tone"] == "gray"


def test_analytics_api_empty_events_with_secret_returns_zero_summary(monkeypatch):
    """秘密已設定但尚無事件時，儀表板回傳零/空狀態而非假造資料。"""
    body = make_admin_client(monkeypatch).get(
        "/admin/api/analytics?range=today").get_json()
    assert body["analytics_enabled"] is True
    assert body["summary"]["completed_queries"] == 0
    assert body["summary"]["query_success_rate"] is None
    assert body["summary"]["navigation_click_rate"] is None
    assert body["summary"]["anonymous_query_devices"] == 0
    assert body["summary"]["districts"] == []
    assert body["summary"]["place_types"] == []


def test_analytics_api_returns_503_when_database_read_fails(monkeypatch):
    """資料庫讀取失敗時必須回傳固定 503，不能把故障偽裝成零流量。"""
    client = make_admin_client(monkeypatch, database_error=True)

    response = client.get("/admin/api/analytics?range=7d")

    assert response.status_code == 503
    assert response.get_json() == {"error": "暫時無法取得分析資料"}
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Robots-Tag"] == "noindex"
    assert client.connections[-1].closed is True


def test_analytics_api_returns_real_summary_and_closes_connection(monkeypatch):
    selected = [
        query_row("query_completed", "req-a", "a" * 64, "success", 100, 3,
                  datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc)),
        query_row("query_completed", "req-b", "b" * 64, "success", 200, 1,
                  datetime(2026, 8, 23, 1, 0, tzinfo=timezone.utc)),
    ]
    nav = [nav_row("req-a", "a" * 64, 1,
                   datetime(2026, 8, 23, 0, 10, tzinfo=timezone.utc))]
    rolling = selected + [
        query_row("query_completed", "req-c", "c" * 64, "success", 1000, 1,
                  datetime(2026, 7, 31, 17, 0, tzinfo=timezone.utc),
                  district="萬華區"),
        query_row("query_completed", "req-d", "a" * 64, "success", 2000, 1,
                  datetime(2026, 8, 1, 17, 0, tzinfo=timezone.utc),
                  district="大安區"),
    ]
    client = make_admin_client(
        monkeypatch,
        analytics_queues=[selected, nav, rolling],
    )

    body = client.get("/admin/api/analytics?range=7d").get_json()

    assert body["range"] == "7d"
    assert body["analytics_enabled"] is True
    summary = body["summary"]
    assert summary["completed_queries"] == 2
    assert summary["query_success_rate"] == 100.0
    assert summary["navigation_click_rate"] == 50.0
    assert summary["click_rank_counts"] == {"1": 1, "2": 0, "3": 0}
    assert summary["anonymous_query_devices"] == 2
    assert summary["repeat_use_rate"] == 1 / 3 * 100
    assert summary["response_median_ms"] == 600.0
    assert summary["response_p95_ms"] == 2000
    assert summary["navigation_provisional"] is True
    connection = client.connections[-1]
    assert connection.closed is True
    start = datetime(2026, 8, 16, 16, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 23, 16, 0, tzinfo=timezone.utc)
    rolling_start = datetime(2026, 7, 24, 16, 0, tzinfo=timezone.utc)
    assert connection.executions[0][1] == (start, end)
    assert connection.executions[1][1] == (
        start, end + timedelta(hours=24))
    assert connection.executions[2][1] == (rolling_start, NOW_UTC)


def detail_row(request_id, occurred_at, device, district=None,
               destination_label=None, outcome_code="success",
               feedback_code=None, raw_query_text=None, **timings):
    """建構 fetch_insight_details 回傳的 26 鍵查詢明細列。"""
    row = {
        "request_id": request_id,
        "occurred_at": occurred_at,
        "anonymous_id_hash": device,
        "source": "direct",
        "query_mode": "manual",
        "raw_query_text": raw_query_text,
        "parsed_query_json": None,
        "destination_label": destination_label,
        "district": district,
        "arrival_time": None,
        "intent": "recommend",
        "outcome_code": outcome_code,
        "error_stage": None,
        "fallback_reason": None,
        "data_status": "fresh",
        "result_count": 3,
        "location_choice_count": 0,
        "parse_ms": None,
        "geocode_ms": None,
        "freshness_ms": None,
        "database_ms": None,
        "walking_ms": None,
        "total_ms": 100,
        "official_data_at": None,
        "collected_at": None,
        "feedback_code": feedback_code,
    }
    row.update(timings)
    return row


def recommendation_row(request_id, lot_id, lot_name, rank, occurred_at):
    """建構 fetch_insight_recommendations 回傳的 18 鍵推薦快照列。"""
    return {
        "request_id": request_id,
        "rank_position": rank,
        "occurred_at": occurred_at,
        "parking_lot_id": lot_id,
        "lot_name": lot_name,
        "recommendation_group": "recommended",
        "available_spaces": 10,
        "total_spaces": 100,
        "pressure_label": "comfortable",
        "decision_status": "recommended",
        "straight_distance_m": 200,
        "walking_distance_m": None,
        "walking_minutes": None,
        "distance_source": "straight_line",
        "hourly_fee_label": "20 元/時",
        "daily_cap_label": None,
        "facility_type_label": "立體停車場",
        "navigation_clicked_at": None,
    }


def choice_row(request_id, device, event_type, occurred_at):
    """建構固定 16 鍵的地點確認事件列。"""
    return {
        "event_type": event_type,
        "occurred_at": occurred_at,
        "request_id": request_id,
        "anonymous_id_hash": device,
        "district": None,
        "area_bucket": None,
        "place_type": None,
        "query_mode": "manual",
        "outcome_code": None,
        "duration_ms": None,
        "result_count": None,
        "clicked_rank": None,
        "parking_lot_id": None,
        "walking_minutes": None,
        "availability_bucket": None,
        "source": "direct",
    }


def test_analytics_api_returns_bounded_insights_with_single_queries(monkeypatch):
    """insights 只查明細與推薦各一次，且整份回應維持 JSON 可序列化。"""
    selected = [
        query_row("query_completed", "req-a", "a" * 64, "success", 100, 3,
                  datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc)),
        query_row("query_completed", "req-b", "b" * 64, "success", 200, 1,
                  datetime(2026, 8, 23, 1, 0, tzinfo=timezone.utc)),
    ]
    nav = [nav_row("req-a", "a" * 64, 1,
                   datetime(2026, 8, 23, 0, 10, tzinfo=timezone.utc))]
    rolling = selected + [
        query_row("query_completed", "req-c", "c" * 64, "success", 1000, 1,
                  datetime(2026, 7, 31, 17, 0, tzinfo=timezone.utc),
                  district="萬華區"),
    ]
    details = [
        detail_row("req-a", datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc),
                   "a" * 64, district="中正區", destination_label="台北車站",
                   feedback_code="found_space", raw_query_text="台北車站",
                   parse_ms=10, geocode_ms=200, freshness_ms=2,
                   database_ms=30, walking_ms=500),
        detail_row("req-b", datetime(2026, 8, 23, 1, 0, tzinfo=timezone.utc),
                   "b" * 64, district="中正區", destination_label="台北車站",
                   raw_query_text="台北車站", parse_ms=8, geocode_ms=150,
                   freshness_ms=3, database_ms=40, walking_ms=600),
    ]
    recommendations = [
        recommendation_row("req-a", "TPE0001", "台北車站地下停車場", 1,
                           datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc)),
        recommendation_row("req-b", "TPE0003", "京站停車場", 2,
                           datetime(2026, 8, 23, 1, 0, tzinfo=timezone.utc)),
    ]
    client = make_admin_client(
        monkeypatch,
        analytics_queues=[selected, nav, rolling, details, recommendations],
        segment_min_devices=1,
    )

    body = client.get("/admin/api/analytics?range=7d").get_json()

    insights = body["insights"]
    assert insights["funnel"] == {
        "completed": 2, "location_choices": 0, "navigations": 1,
        "feedback": 1,
    }
    assert insights["districts"] == [{"district": "中正區", "queries": 2}]
    assert insights["feedback"] == {
        "found_space": 1, "full_on_arrival": 0, "did_not_go": 0,
    }
    assert insights["destinations"] == [
        {"destination": "台北車站", "queries": 2},
    ]
    assert insights["stage_timings"] == {
        "parse_ms": 9, "geocode_ms": 175, "freshness_ms": 2,
        "database_ms": 35, "walking_ms": 550,
    }
    assert len(insights["destinations"]) <= 10
    assert len(insights["lots"]) <= 10
    assert len(insights["recent_queries"]) <= 20
    assert isinstance(insights["recent_queries"][0]["occurred_at"], str)
    assert "anonymous_id_hash" not in insights["recent_queries"][0]
    assert "parsed_query_json" not in insights["recent_queries"][0]

    connection = client.connections[-1]
    executions = connection.executions
    detail_calls = [
        (sql, params) for sql, params in executions
        if "FROM analytics_query_details" in sql
    ]
    recommendation_calls = [
        (sql, params) for sql, params in executions
        if "FROM analytics_recommendations" in sql
    ]
    assert len(detail_calls) == 1
    assert len(recommendation_calls) == 1
    assert "LIMIT" not in detail_calls[0][0]
    start = datetime(2026, 8, 16, 16, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 23, 16, 0, tzinfo=timezone.utc)
    assert detail_calls[0][1] == (start, end)
    assert recommendation_calls[0][1] == (start, end)
    assert connection.closed is True


def test_analytics_api_segments_use_config_min_devices(monkeypatch):
    """行政區是否列出由 ANALYTICS_SEGMENT_MIN_DEVICES 設定決定。"""
    details = [
        detail_row(f"req-{index}", NOW_UTC, f"d{index}".ljust(64, "0"),
                   district="中正區")
        for index in range(3)
    ]
    queues = [[], [], [], details, []]

    shown = make_admin_client(
        monkeypatch, analytics_queues=list(queues),
        segment_min_devices=1).get("/admin/api/analytics?range=today")
    hidden = make_admin_client(
        monkeypatch, analytics_queues=list(queues),
        segment_min_devices=5).get("/admin/api/analytics?range=today")

    assert shown.get_json()["insights"]["districts"] == [
        {"district": "中正區", "queries": 3},
    ]
    assert hidden.get_json()["insights"]["districts"] == []
