"""儀表板指標測試：鎖定分母、首次點擊、臺北日期邊界與樣本門檻。"""

from datetime import datetime, timedelta, timezone

import pytest

from analytics_database import fetch_dashboard_events
from analytics_service import parse_dashboard_range, summarize_events

NOW_UTC = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)


def query_row(event_type, request_id, device, outcome_code, duration_ms,
              result_count, occurred_at, district="中正區",
              place_type="station"):
    """建構 fetch_events 回傳的 16 鍵查詢事件列。"""
    return {
        "event_type": event_type,
        "occurred_at": occurred_at,
        "request_id": request_id,
        "anonymous_id_hash": device,
        "district": district,
        "area_bucket": None,
        "place_type": place_type,
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


def metric_fixture_rows():
    """固定指標樣本：3 完成 1 失敗、2 個首次點擊與 1 筆重複點擊。"""
    return [
        query_row("query_completed", "req-1", "a" * 64, "success", 100, 3,
                  datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc)),
        query_row("query_completed", "req-2", "a" * 64,
                  "degraded_stale_data", 200, 2,
                  datetime(2026, 8, 23, 1, 0, tzinfo=timezone.utc)),
        query_row("query_completed", "req-3", "b" * 64, "success", 300, 1,
                  datetime(2026, 8, 23, 2, 0, tzinfo=timezone.utc)),
        query_row("query_failed", "req-4", "c" * 64, "failed_validation",
                  50, 0, datetime(2026, 8, 23, 3, 0, tzinfo=timezone.utc),
                  district="萬華區", place_type="mall"),
        nav_row("req-1", "a" * 64, 1,
                datetime(2026, 8, 23, 0, 10, tzinfo=timezone.utc)),
        nav_row("req-2", "a" * 64, 2,
                datetime(2026, 8, 23, 2, 30, tzinfo=timezone.utc)),
        nav_row("req-2", "a" * 64, 3,
                datetime(2026, 8, 23, 3, 30, tzinfo=timezone.utc)),
        nav_row("req-3", "b" * 64, 1,
                datetime(2026, 8, 25, 0, 0, tzinfo=timezone.utc)),
    ]


def recent_query_rows():
    """最近 24 小時內發生的查詢與點擊，觀察窗尚未結束。"""
    return [
        query_row("query_completed", "req-now", "d" * 64, "success", 120, 2,
                  datetime(2026, 8, 23, 7, 55, tzinfo=timezone.utc)),
        nav_row("req-now", "d" * 64, 1,
                datetime(2026, 8, 23, 7, 56, tzinfo=timezone.utc)),
    ]


def test_summary_uses_locked_denominators_and_first_navigation_click():
    rows = metric_fixture_rows()
    summary = summarize_events(rows, NOW_UTC, min_devices=2)
    assert summary["completed_queries"] == 3
    assert summary["query_success_rate"] == 75.0
    assert summary["navigation_click_rate"] == 2 / 3 * 100
    assert summary["click_rank_counts"] == {"1": 1, "2": 1, "3": 0}
    assert summary["anonymous_query_devices"] == 3
    assert summary["degraded_queries"] == 1


def test_recent_navigation_window_is_marked_provisional():
    summary = summarize_events(recent_query_rows(), NOW_UTC)
    assert summary["navigation_provisional"] is True


def test_recent_failed_or_resultless_query_does_not_mark_provisional():
    """暫估旗標只考慮有結果的合格完成查詢，失敗查詢不得觸發。"""
    rows = [
        query_row("query_failed", "req-f", "e" * 64, "failed_validation",
                  50, 0, datetime(2026, 8, 23, 7, 55, tzinfo=timezone.utc)),
        query_row("query_completed", "req-0", "f" * 64, "success",
                  50, 0, datetime(2026, 8, 23, 7, 56, tzinfo=timezone.utc)),
    ]
    assert summarize_events(rows, NOW_UTC)["navigation_provisional"] is False


def test_rank_zero_clicks_count_in_rate_but_not_rank_shares():
    """rank 0（其他場站）要計入點擊率，但不得進入 1/2/3 名次占比。"""
    rows = [
        query_row("query_completed", "req-z", "a" * 64, "success", 100, 3,
                  datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)),
        nav_row("req-z", "a" * 64, 0,
                datetime(2026, 8, 23, 8, 10, tzinfo=timezone.utc)),
    ]
    summary = summarize_events(rows, NOW_UTC)
    assert summary["navigation_click_rate"] == 100.0
    assert summary["click_rank_counts"] == {"1": 0, "2": 0, "3": 0}


def test_completed_observation_window_is_not_provisional():
    old_rows = [
        query_row("query_completed", "req-old", "e" * 64, "success", 90, 1,
                  datetime(2026, 8, 22, 7, 0, tzinfo=timezone.utc)),
    ]
    assert summarize_events(old_rows, NOW_UTC)["navigation_provisional"] is False


def test_segments_hide_districts_and_place_types_below_min_devices():
    summary = summarize_events(metric_fixture_rows(), NOW_UTC, min_devices=2)
    assert summary["districts"] == [{"district": "中正區", "devices": 2}]
    assert summary["place_types"] == [{"place_type": "station", "devices": 2}]
    assert summarize_events(metric_fixture_rows(), NOW_UTC)["districts"] == []


def test_segment_threshold_hides_four_devices_shows_five():
    """預設樣本下限 5：四裝置行政區隱藏，五裝置行政區出現。"""
    rows = [
        query_row(
            "query_completed", f"req-4-{index}",
            f"d4-{index}".ljust(64, "0"), "success", 100, 1,
            datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc),
            district="四甲區")
        for index in range(4)
    ]
    rows += [
        query_row(
            "query_completed", f"req-5-{index}",
            f"d5-{index}".ljust(64, "0"), "success", 100, 1,
            datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc),
            district="五乙區")
        for index in range(5)
    ]
    summary = summarize_events(rows, NOW_UTC)
    assert summary["districts"] == [{"district": "五乙區", "devices": 5}]


def test_response_durations_use_median_and_nearest_rank_p95():
    summary = summarize_events(metric_fixture_rows(), NOW_UTC)
    assert summary["response_median_ms"] == 150.0
    assert summary["response_p95_ms"] == 300


def test_repeat_use_requires_two_distinct_taipei_dates():
    rows = [
        query_row("query_completed", "req-r1", "f" * 64, "success", 10, 1,
                  datetime(2026, 8, 23, 0, 30, tzinfo=timezone.utc)),
        query_row("query_completed", "req-r2", "f" * 64, "success", 10, 1,
                  datetime(2026, 8, 23, 15, 30, tzinfo=timezone.utc)),
        query_row("query_completed", "req-r3", "g" * 64, "success", 10, 1,
                  datetime(2026, 7, 25, 17, 0, tzinfo=timezone.utc)),
        query_row("query_completed", "req-r4", "g" * 64, "success", 10, 1,
                  datetime(2026, 8, 1, 17, 0, tzinfo=timezone.utc)),
    ]
    summary = summarize_events(rows, NOW_UTC)
    assert summary["repeat_use_rate"] == 1 / 2 * 100


def test_rolling_30d_rows_drive_repeat_use_and_durations_only():
    selected = [
        query_row("query_completed", "req-t1", "x" * 64, "success", 100, 1,
                  datetime(2026, 8, 23, 0, 30, tzinfo=timezone.utc)),
        query_row("query_completed", "req-t2", "y" * 64, "success", 200, 1,
                  datetime(2026, 8, 23, 1, 30, tzinfo=timezone.utc)),
        nav_row("req-t1", "x" * 64, 1,
                datetime(2026, 8, 23, 0, 40, tzinfo=timezone.utc)),
    ]
    rolling = selected + [
        query_row("query_completed", "req-r5", "x" * 64, "success", 1000, 1,
                  datetime(2026, 7, 31, 17, 0, tzinfo=timezone.utc)),
        query_row("query_completed", "req-r6", "z" * 64, "success", 2000, 1,
                  datetime(2026, 8, 1, 17, 0, tzinfo=timezone.utc)),
    ]
    summary = summarize_events(selected, NOW_UTC, rolling_30d_rows=rolling)
    assert summary["repeat_use_rate"] == 1 / 3 * 100
    assert summary["response_median_ms"] == 600.0
    assert summary["response_p95_ms"] == 2000
    assert summary["completed_queries"] == 2
    assert summary["query_success_rate"] == 100.0
    assert summary["navigation_click_rate"] == 50.0
    assert summary["click_rank_counts"] == {"1": 1, "2": 0, "3": 0}
    assert summary["anonymous_query_devices"] == 2
    fallback = summarize_events(selected, NOW_UTC)
    assert fallback["repeat_use_rate"] == 0.0
    assert fallback["response_median_ms"] == 150.0
    assert fallback["response_p95_ms"] == 200


def test_summary_accepts_naive_utc_rows_from_database():
    rows = recent_query_rows()
    for row in rows:
        row["occurred_at"] = row["occurred_at"].replace(tzinfo=None)
    assert summarize_events(rows, NOW_UTC)["navigation_provisional"] is True


def test_empty_summary_returns_safe_none_and_zero_values():
    summary = summarize_events([], NOW_UTC)
    assert summary["completed_queries"] == 0
    assert summary["query_success_rate"] is None
    assert summary["navigation_click_rate"] is None
    assert summary["click_rank_counts"] == {"1": 0, "2": 0, "3": 0}
    assert summary["anonymous_query_devices"] == 0
    assert summary["repeat_use_rate"] is None
    assert summary["response_median_ms"] is None
    assert summary["response_p95_ms"] is None
    assert summary["districts"] == []
    assert summary["place_types"] == []


def test_today_range_uses_taipei_midnight_but_returns_utc():
    start, end = parse_dashboard_range(
        "today", datetime(2026, 8, 23, 15, 30, tzinfo=timezone.utc)
    )
    assert start == datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 23, 16, 0, tzinfo=timezone.utc)


def test_wider_ranges_align_to_taipei_midnight_in_utc():
    now = datetime(2026, 8, 23, 15, 30, tzinfo=timezone.utc)
    assert parse_dashboard_range("7d", now) == (
        datetime(2026, 8, 16, 16, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 23, 16, 0, tzinfo=timezone.utc),
    )
    assert parse_dashboard_range("30d", now) == (
        datetime(2026, 7, 24, 16, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 23, 16, 0, tzinfo=timezone.utc),
    )


def test_parse_dashboard_range_rejects_unknown_value():
    with pytest.raises(ValueError):
        parse_dashboard_range("90d", NOW_UTC)


class _RecordingCursor:
    """依呼叫順序回傳各次查詢結果並記錄 SQL 與參數。"""

    def __init__(self, row_sets):
        self.row_sets = list(row_sets)
        self.executions = []

    def execute(self, sql, params=None):
        self.executions.append((sql, params))

    def fetchall(self):
        return self.row_sets.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _RecordingConnection:
    """提供共用執行紀錄的假連線。"""

    def __init__(self, row_sets):
        self.cursor_instance = _RecordingCursor(row_sets)

    def cursor(self):
        return self.cursor_instance


def test_dashboard_fetch_extends_navigation_window_past_range_end():
    start = datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 23, 16, 0, tzinfo=timezone.utc)
    query = query_row("query_completed", "req-db", "h" * 64, "success",
                      80, 1, datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc))
    nav = nav_row("req-db", "h" * 64, 1,
                  datetime(2026, 8, 23, 23, 0, tzinfo=timezone.utc))
    connection = _RecordingConnection([[query], [nav]])
    rows = fetch_dashboard_events(connection, start, end)
    query_sql, query_params = connection.cursor_instance.executions[0]
    nav_sql, nav_params = connection.cursor_instance.executions[1]
    assert "event_type IN ('query_completed', 'query_failed')" in query_sql
    assert "occurred_at >= %s AND occurred_at < %s" in query_sql
    assert query_params == (start, end)
    assert "event_type = 'navigation_clicked'" in nav_sql
    assert nav_params == (start, end + timedelta(hours=24))
    assert rows == [query, nav]
    summary = summarize_events(rows, NOW_UTC)
    assert summary["navigation_click_rate"] == 100.0
