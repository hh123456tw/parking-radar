"""停車地獄雷達核心測試；所有外部服務都使用固定資料或 mock。"""

from app import create_app
from config import Config
from zoneinfo import ZoneInfo


def test_config_has_locked_analysis_constants():
    """搜尋半徑與新鮮度必須符合設計規格，不受本機環境影響。"""
    assert Config.SEARCH_RADIUS_M == 1500
    assert Config.FRESHNESS_MINUTES == 45


def test_health_route_returns_ok():
    """健康檢查不依賴資料庫或外部 API。"""
    app = create_app({"TESTING": True, "SECRET_KEY": "test"})
    response = app.test_client().get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


from analysis import clean_available, district_hell_score, hell_label, hell_score


def test_clean_available_rejects_special_and_impossible_values():
    """官方負數、缺值與剩餘數超過總數都不能進入計算。"""
    assert clean_available(100, -9) is None
    assert clean_available(100, None) is None
    assert clean_available(100, 101) is None
    assert clean_available(0, 0) is None
    assert clean_available(100, 20) == 20


def test_hell_score_and_label_use_fixed_thresholds():
    assert hell_score(200, 10) == 95.0
    assert hell_label(95.0) == "停車地獄"
    assert hell_label(80.0) == "很難停"
    assert hell_label(60.0) == "開始擠"
    assert hell_label(59.9) == "輕鬆停"


def test_district_score_is_weighted_by_spaces():
    rows = [
        {"total_spaces": 100, "available_spaces": 50},
        {"total_spaces": 300, "available_spaces": 30},
        {"total_spaces": 50, "available_spaces": -9},
    ]
    assert district_hell_score(rows) == 80.0


from analysis import distance_ease, haversine_m, rank_candidates, recommendation_score


def test_haversine_and_radius_filter():
    distance = haversine_m(25.0330, 121.5654, 25.0330, 121.5704)
    assert 490 < distance < 520
    rows = [
        {"lot_id": "near", "latitude": 25.0330, "longitude": 121.5704,
         "total_spaces": 100, "available_spaces": 20},
        {"lot_id": "far", "latitude": 25.0330, "longitude": 121.5904,
         "total_spaces": 100, "available_spaces": 50},
    ]
    ranked = rank_candidates(rows, 25.0330, 121.5654)
    assert [row["lot_id"] for row in ranked] == ["near"]


def test_recommendation_weights_with_and_without_history():
    # 即時地獄 80 → 容易度 20；距離 750m → 容易度 50；歷史地獄 60 → 40。
    assert recommendation_score(80, 750, 60) == 33.0
    assert recommendation_score(80, 750, None) == 32.0
    assert distance_ease(1500) == 0.0


from database import fetch_current_lots, insert_snapshots


class FakeCursor:
    """記錄 SQL 與參數，讓測試不需要真的啟動 MySQL。"""
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def executemany(self, sql, params):
        values = list(params)
        self.calls.append((sql, values))
        self.rowcount = len(values)

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeConnection:
    def __init__(self, rows=None):
        self.fake_cursor = FakeCursor(rows)

    def cursor(self):
        return self.fake_cursor


def test_insert_snapshots_uses_bulk_parameterized_sql():
    connection = FakeConnection()
    count = insert_snapshots(connection, [{
        "lot_id": "TPE0001", "available_spaces": 8,
        "source_updated_at": "2026-08-03 10:00:00",
        "captured_at": "2026-08-03 10:01:00",
    }])
    sql, params = connection.fake_cursor.calls[0]
    assert "%s" in sql and "ON DUPLICATE KEY" in sql
    assert params[0][0] == "TPE0001"
    assert count == 1


def test_fetch_current_lots_passes_freshness_and_district_as_parameters():
    connection = FakeConnection([{"lot_id": "TPE0001"}])
    rows = fetch_current_lots(connection, "信義區", freshness_minutes=45)
    sql, params = connection.fake_cursor.calls[0]
    assert "ROW_NUMBER()" in sql
    assert params == (45, "信義區")
    assert rows == [{"lot_id": "TPE0001"}]


import json
from datetime import datetime, timezone
from pathlib import Path
from collector import parse_dynamic, parse_static

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name):
    """讀取固定官方格式，讓測試不依賴網路內容。"""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_static_parser_uses_exact_id_and_wgs84_entrance():
    payload = load_fixture("taipei_static.json")
    lots = parse_static(payload, {"TPE0001"})
    assert lots[0]["lot_id"] == "TPE0001"
    assert lots[0]["operator_type"] == "民營停車場"
    assert lots[0]["latitude"] == 25.0552
    assert lots[0]["longitude"] == 121.5242
    assert lots[0]["supports_realtime"] is True
    assert lots[1]["supports_realtime"] is False


def test_dynamic_parser_keeps_only_nonnegative_values():
    payload = load_fixture("taipei_dynamic.json")
    captured = datetime(2026, 8, 3, 10, 1, tzinfo=timezone.utc)
    snapshots = parse_dynamic(payload, captured)
    assert snapshots == [{
        "lot_id": "TPE0001", "available_spaces": 8,
        "source_updated_at": datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc),
        "captured_at": captured,
    }, {
        "lot_id": "TPE0003", "available_spaces": 999,
        "source_updated_at": datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc),
        "captured_at": captured,
    }]


import collector


def test_collect_once_filters_available_over_total_and_commits(monkeypatch):
    static = load_fixture("taipei_static.json")
    dynamic = load_fixture("taipei_dynamic.json")
    connection = type("Connection", (), {
        "committed": False, "rolled_back": False,
        "commit": lambda self: setattr(self, "committed", True),
        "rollback": lambda self: setattr(self, "rolled_back", True),
        "close": lambda self: None,
    })()
    monkeypatch.setattr(collector, "fetch_json", lambda url, timeout=15: static if "alldesc" in url else dynamic)
    monkeypatch.setattr(collector, "get_connection", lambda: connection)
    monkeypatch.setattr(collector, "upsert_parking_lots", lambda conn, rows: len(rows))
    saved = []
    monkeypatch.setattr(collector, "insert_snapshots", lambda conn, rows: saved.extend(rows) or len(rows))
    result = collector.collect_once()
    assert [row["lot_id"] for row in saved] == ["TPE0001"]
    assert result == {"lots": 2, "snapshots": 1}
    assert connection.committed is True


from datetime import timedelta
from analysis import (build_history_series, summarize_hour_comparison,
                      summarize_matching_history)


def history_row(local_day, hour, available):
    """建立臺北時間樣本，再轉成資料庫使用的 UTC。"""
    local = datetime(2026, 8, local_day, hour, tzinfo=ZoneInfo("Asia/Taipei"))
    return {"captured_at": local.astimezone(timezone.utc),
            "total_spaces": 100, "available_spaces": available}


def test_history_requires_three_same_day_type_and_hour_samples():
    arrival = datetime(2026, 8, 8, 18, tzinfo=ZoneInfo("Asia/Taipei"))
    insufficient = [history_row(1, 18, 10), history_row(2, 18, 20)]
    assert summarize_matching_history(insufficient, arrival)["hell_score"] is None
    enough = insufficient + [history_row(8, 18, 30)]
    summary = summarize_matching_history(enough, arrival)
    assert summary == {"hell_score": 80.0, "sample_count": 3, "day_type": "weekend", "hour": 18}


def test_history_series_has_iso_time_and_available_spaces():
    rows = [history_row(1, 18, 10)]
    point = build_history_series(rows)[0]
    assert point["available_spaces"] == 10
    assert point["captured_at"].endswith("+08:00")


def test_weekday_weekend_comparison_reports_both_groups():
    rows = [history_row(day, 18, 50) for day in (3, 4, 5)]
    rows += [history_row(day, 18, 20) for day in (1, 2, 8)]
    comparison = summarize_hour_comparison(rows, 18)
    assert comparison["weekday"] == {"hell_score": 50.0, "sample_count": 3}
    assert comparison["weekend"] == {"hell_score": 80.0, "sample_count": 3}
