"""資料庫與蒐集器測試：驗證 SQL 參數、資料清洗及交易邊界。"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import collector
import database

FIXTURES = Path(__file__).parent / "fixtures"


class SpyCursor:
    """記錄 SQL 呼叫並提供可控制的查詢結果。"""

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


class SpyConnection:
    """提供 database.py 所需的最小 cursor 介面。"""

    def __init__(self, rows=None):
        self.spy_cursor = SpyCursor(rows)

    def cursor(self):
        return self.spy_cursor


class TransactionConnection:
    """記錄 collector 是否正確 commit、rollback 與 close。"""

    def __init__(self):
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def load_fixture(name):
    """每次重新讀取 fixture，避免測試之間共享可變資料。"""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def sample_lot():
    """建立包含完整資料庫欄位的停車場資料。"""
    return {
        "lot_id": "TPE0001", "lot_name": "測試停車場", "district": "信義區",
        "address": "市府路1號", "operator_type": "民營停車場",
        "total_spaces": 100, "fee_info": "每小時30元", "service_time": "24小時",
        "latitude": 25.0375, "longitude": 121.5637, "supports_realtime": True,
        "source_updated_at": "2026-08-04 10:00:00",
    }


def test_get_connection_uses_locked_mysql_options(monkeypatch):
    """連線必須使用 DictCursor、utf8mb4 與手動交易。"""
    captured = {}
    sentinel = object()
    monkeypatch.setattr(database.pymysql, "connect",
                        lambda **kwargs: captured.update(kwargs) or sentinel)

    assert database.get_connection() is sentinel
    assert captured["charset"] == "utf8mb4"
    assert captured["autocommit"] is False
    assert captured["cursorclass"] is database.pymysql.cursors.DictCursor


def test_upsert_parking_lots_binds_complete_row_as_parameters():
    """停車場內容只能出現在參數中，不得插入 SQL 字串。"""
    connection = SpyConnection()

    count = database.upsert_parking_lots(connection, [sample_lot()])

    sql, values = connection.spy_cursor.calls[0]
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "測試停車場" not in sql
    assert values[0][0:4] == ("TPE0001", "測試停車場", "信義區", "市府路1號")
    assert count == 1


def test_insert_snapshots_uses_bulk_parameterized_sql():
    """快照內容應批次綁定，重複官方時間不得新增第二筆。"""
    connection = SpyConnection()
    snapshot = {
        "lot_id": "TPE0001", "available_spaces": 8,
        "source_updated_at": "2026-08-04 10:00:00",
        "captured_at": "2026-08-04 10:01:00",
    }

    count = database.insert_snapshots(connection, [snapshot])

    sql, values = connection.spy_cursor.calls[0]
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert values == [("TPE0001", 8, "2026-08-04 10:00:00", "2026-08-04 10:01:00")]
    assert count == 1


def test_fetch_current_lots_supports_all_city_and_district_queries():
    """行政區存在時才加入條件，兩種模式都必須保留新鮮度參數。"""
    city_connection = SpyConnection([])
    database.fetch_current_lots(city_connection, freshness_minutes=45)
    city_sql, city_params = city_connection.spy_cursor.calls[0]
    assert "ROW_NUMBER()" in city_sql
    assert "s.source_updated_at AS snapshot_updated_at" in city_sql
    assert "AND district = %s" not in city_sql
    assert city_params == (45,)

    district_connection = SpyConnection([{"lot_id": "TPE0001"}])
    rows = database.fetch_current_lots(
        district_connection, "信義區", freshness_minutes=45)
    district_sql, district_params = district_connection.spy_cursor.calls[0]
    assert "AND district = %s" in district_sql
    assert district_params == (45, "信義區")
    assert rows == [{"lot_id": "TPE0001"}]


def test_latest_snapshot_and_stale_fallback_queries():
    """新鮮度安全網可讀最後時間，降級查詢則不得保留時間門檻。"""
    captured_at = datetime(2026, 8, 4, 6, 0)
    time_connection = SpyConnection([{"captured_at": captured_at}])
    assert database.fetch_latest_snapshot_time(time_connection) == captured_at
    assert "MAX(captured_at)" in time_connection.spy_cursor.calls[0][0]

    stale_connection = SpyConnection([{"lot_id": "TPE0001"}])
    rows = database.fetch_current_lots(
        stale_connection, "信義區", freshness_minutes=None)
    sql, params = stale_connection.spy_cursor.calls[0]
    assert "UTC_TIMESTAMP()" not in sql
    assert params == ("信義區",)
    assert rows == [{"lot_id": "TPE0001"}]


def test_fetch_history_uses_lot_and_time_parameters():
    """歷史查詢不得把 URL 中的 lot_id 拼接進 SQL。"""
    connection = SpyConnection([{"lot_id": "TPE0001"}])
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 8, 4, tzinfo=timezone.utc)

    rows = database.fetch_history(connection, "TPE0001", start, end)

    sql, params = connection.spy_cursor.calls[0]
    assert "TPE0001" not in sql
    assert params == ("TPE0001", start, end)
    assert rows == [{"lot_id": "TPE0001"}]


def test_fetch_matching_history_handles_empty_and_multiple_ids():
    """空候選不查 DB，多場站則產生相同數量的安全 placeholder。"""
    empty_connection = SpyConnection()
    assert database.fetch_matching_history(empty_connection, [], "start", "end") == []
    assert empty_connection.spy_cursor.calls == []

    connection = SpyConnection()
    database.fetch_matching_history(connection, ["TPE1", "TPE2"], "start", "end")
    sql, params = connection.spy_cursor.calls[0]
    assert "IN(%s,%s)" in "".join(sql.split())
    assert params == ("TPE1", "TPE2", "start", "end")


def test_geocode_cache_queries_and_saves_with_parameters():
    """地址快取讀寫都必須使用主鍵參數，不得拼接地址。"""
    cached = {"normalized_address": "臺北市信義區市府路1號"}
    read_connection = SpyConnection([cached])
    assert database.get_cached_geocode(read_connection, cached["normalized_address"]) == cached
    assert read_connection.spy_cursor.calls[0][1] == (cached["normalized_address"],)

    write_connection = SpyConnection()
    row = dict(cached, display_address="臺北市政府", latitude=25.0375,
               longitude=121.5637, cached_at="2026-08-04 10:00:00")
    database.save_cached_geocode(write_connection, row)
    sql, params = write_connection.spy_cursor.calls[0]
    assert row["normalized_address"] not in sql
    assert params[0] == row["normalized_address"]


def test_collect_once_rejects_available_over_its_own_total(monkeypatch):
    """同 ID 的剩餘格數大於總格數時，不得寫入快照。"""
    static = load_fixture("taipei_static.json")
    dynamic = load_fixture("taipei_dynamic.json")
    static["data"]["park"].append({
        "id": "TPE0003", "area": "信義區", "name": "超額測試場",
        "address": "測試路3號", "totalcar": 100, "EntranceCoord": {},
    })
    connection = TransactionConnection()
    saved = []
    monkeypatch.setattr(collector, "fetch_json",
                        lambda url, timeout=15: static if "alldesc" in url else dynamic)
    monkeypatch.setattr(collector, "get_connection", lambda: connection)
    monkeypatch.setattr(collector, "upsert_parking_lots", lambda conn, rows: len(rows))
    monkeypatch.setattr(collector, "insert_snapshots",
                        lambda conn, rows: saved.extend(rows) or len(rows))

    result = collector.collect_once()

    assert [row["lot_id"] for row in saved] == ["TPE0001"]
    assert result == {"lots": 3, "snapshots": 1}
    assert connection.committed is True
    assert connection.closed is True


def test_collect_once_rolls_back_and_closes_when_insert_fails(monkeypatch):
    """快照寫入失敗時不得 commit，且必須 rollback、close 並重拋例外。"""
    static = load_fixture("taipei_static.json")
    dynamic = load_fixture("taipei_dynamic.json")
    connection = TransactionConnection()
    monkeypatch.setattr(collector, "fetch_json",
                        lambda url, timeout=15: static if "alldesc" in url else dynamic)
    monkeypatch.setattr(collector, "get_connection", lambda: connection)
    monkeypatch.setattr(collector, "upsert_parking_lots", lambda conn, rows: len(rows))
    monkeypatch.setattr(
        collector, "insert_snapshots",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("insert failed")),
    )

    with pytest.raises(RuntimeError, match="insert failed"):
        collector.collect_once()

    assert connection.committed is False
    assert connection.rolled_back is True
    assert connection.closed is True
