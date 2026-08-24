"""分析資料庫測試：固定 16 欄參數化、導航驗證與 UTC 邊界。"""

from datetime import datetime, timezone

from analytics_database import (
    delete_expired_events,
    fetch_events,
    fetch_status_times,
    insert_event,
    insert_navigation_event,
)

VALID_REQUEST_ID = "550e8400-e29b-41d4-a716-446655440000"


class SpyCursor:
    """記錄 execute 呼叫並提供可控的列數與查詢結果。"""

    def __init__(self, rows=None, rowcount=0, executions=None):
        self.rows = rows or []
        self.rowcount = rowcount
        self.executions = executions if executions is not None else []

    def execute(self, sql, params=None):
        self.executions.append((sql, params))

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class SpyConnection:
    """讓 connection 與 cursor 共用同一份 SQL 執行紀錄。"""

    def __init__(self, rows=None, rowcount=0):
        self.executions = []
        self.cursor_instance = SpyCursor(rows, rowcount, self.executions)

    def cursor(self):
        return self.cursor_instance


def sample_query_event():
    """與 analytics_service.build_query_event 相同 16 鍵的查詢事件。"""
    return {
        "event_type": "query_completed",
        "occurred_at": datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc),
        "request_id": VALID_REQUEST_ID,
        "anonymous_id_hash": "a" * 64,
        "district": "台北車站",
        "area_bucket": "25.04,121.53",
        "place_type": "station",
        "query_mode": "manual",
        "outcome_code": "success",
        "duration_ms": 1234,
        "result_count": 3,
        "clicked_rank": None,
        "parking_lot_id": None,
        "walking_minutes": None,
        "availability_bucket": None,
        "source": "direct",
    }


def sample_navigation_event():
    """與前端送出欄位對應的導航事件，request 與 hash 須對應同一次查詢。"""
    event = sample_query_event()
    event.update({
        "event_type": "navigation_clicked",
        "occurred_at": datetime(2026, 8, 23, 8, 5, tzinfo=timezone.utc),
        "district": None,
        "area_bucket": None,
        "place_type": None,
        "query_mode": None,
        "outcome_code": None,
        "duration_ms": None,
        "result_count": None,
        "clicked_rank": 1,
        "parking_lot_id": "TPE0001",
        "walking_minutes": 6.5,
        "availability_bucket": "11_plus",
    })
    return event


def test_insert_event_uses_fixed_parameterized_columns():
    """16 個資料欄位必須固定順序且全部以參數傳入，不得拼接內容。"""
    connection = SpyConnection(rowcount=1)
    count = insert_event(connection, sample_query_event())
    sql, params = connection.cursor_instance.executions[0]
    assert "INSERT INTO analytics_events" in sql
    assert "%s" in sql
    assert "台北車站" not in sql
    assert len(params) == 16
    assert params[0] == "query_completed"
    assert params[1] == datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)
    assert params[3] == "a" * 64
    assert count == 1


def test_navigation_insert_requires_matching_recent_query():
    """導航只從 24 小時內同 hash 的成功查詢複製上下文，並以唯一鍵去重。"""
    connection = SpyConnection(rowcount=1)
    count = insert_navigation_event(connection, sample_navigation_event())
    sql, params = connection.cursor_instance.executions[0]
    assert "INSERT IGNORE INTO analytics_events" in sql
    assert "event_type = 'query_completed'" in sql
    assert "INTERVAL 24 HOUR" in sql
    assert "TPE0001" not in sql
    assert params.count("a" * 64) == 2
    assert count == 1


def test_cleanup_and_fetch_use_bounded_utc_parameters():
    """刪除與讀取都必須以 UTC 半開區間作為參數。"""
    connection = SpyConnection(rows=[])
    cutoff = datetime(2026, 5, 25, tzinfo=timezone.utc)
    delete_expired_events(connection, cutoff)
    fetch_events(connection, cutoff, datetime(2026, 8, 23, tzinfo=timezone.utc))
    assert "occurred_at < %s" in connection.executions[0][0]
    assert "occurred_at >= %s AND occurred_at < %s" in connection.executions[1][0]


def test_delete_expired_events_returns_cutoff_param_and_rowcount():
    """清理回傳受影響列數，cutoff 只能出現在參數中。"""
    connection = SpyConnection(rowcount=7)
    cutoff = datetime(2026, 5, 25, tzinfo=timezone.utc)
    assert delete_expired_events(connection, cutoff) == 7
    sql, params = connection.cursor_instance.executions[0]
    assert params == (cutoff,)
    assert "2026" not in sql


def test_fetch_events_returns_cursor_rows():
    """讀取必須原樣回傳資料庫列，供儀表板彙整使用。"""
    row = {
        "event_type": "query_completed",
        "occurred_at": datetime(2026, 8, 23, tzinfo=timezone.utc),
    }
    connection = SpyConnection(rows=[row])
    start = datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 23, 16, 0, tzinfo=timezone.utc)
    assert fetch_events(connection, start, end) == [row]


def test_fetch_status_times_returns_three_time_keys():
    """狀態時間一次取出官方資料、Collector 與後設資料三個最新時間。"""
    now = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)
    connection = SpyConnection(rows=[{
        "official_data_at": now,
        "collector_at": now,
        "metadata_at": now,
    }])
    assert fetch_status_times(connection) == {
        "official_data_at": now,
        "collector_at": now,
        "metadata_at": now,
    }
    sql, _params = connection.executions[0]
    assert "ORDER BY snapshot_id DESC LIMIT 1" in sql
    assert "MAX(source_updated_at)" not in sql
    assert "MAX(captured_at)" not in sql
