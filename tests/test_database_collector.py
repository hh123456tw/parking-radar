"""資料庫與蒐集器測試：驗證 SQL 參數、資料清洗及交易邊界。"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

import collector
import database
import new_taipei_source

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
        "city": "臺北市", "source": "taipei", "source_lot_id": "TPE0001",
        "latitude": 25.0375, "longitude": 121.5637, "supports_realtime": True,
        "source_updated_at": "2026-08-04 10:00:00",
        "fare_rules_json": '{"FareRule":[]}',
        "facility_type": None,
        "facility_source": None,
        "metadata_checked_at": None,
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
    assert values[0][0] == "TPE0001"
    assert values[0][4:7] == ("測試停車場", "信義區", "市府路1號")
    assert '{"FareRule":[]}' in values[0]
    for column in ("fare_rules_json", "facility_type", "facility_source",
                   "metadata_checked_at"):
        assert column in sql
    assert count == 1


def test_upsert_lots_binds_city_source_and_source_id():
    """城市與來源欄位必須綁定參數，不得拼進 SQL 字串。"""
    connection = SpyConnection([])
    row = sample_lot()
    row.update(city="臺北市", source="taipei", source_lot_id="TPE0001")

    database.upsert_parking_lots(connection, [row])

    sql, values = connection.spy_cursor.calls[0]
    assert "city" in sql and "source" in sql and "source_lot_id" in sql
    assert values[0][:4] == ("TPE0001", "臺北市", "taipei", "TPE0001")


def test_upsert_duplicate_key_preserves_manual_facility_with_case():
    """重複鍵更新必須以固定 CASE 依優先序保留 manual，不得直接覆寫欄位。"""
    connection = SpyConnection()
    database.upsert_parking_lots(connection, [sample_lot()])

    sql, _values = connection.spy_cursor.calls[0]
    assert "facility_type = CASE" in sql
    assert "facility_source = CASE" in sql
    assert "VALUES(facility_source)" in sql
    for keyword in ("'manual'", "'official'", "'osm'", "'unknown'"):
        assert keyword in sql
    for forbidden in ("mechanical", "multi_storey", "underground",
                      "surface", "mixed"):
        assert forbidden not in sql


def test_fetch_parking_metadata_candidates_selects_sync_columns():
    """型態同步需要的欄位必須一次取出。"""
    candidate = {"lot_id": "TPE0001", "lot_name": "測試停車場",
                 "latitude": 25.05, "longitude": 121.53,
                 "facility_type": "unknown", "facility_source": "unknown"}
    connection = SpyConnection([candidate])

    assert database.fetch_parking_metadata_candidates(connection) == [candidate]

    sql, _params = connection.spy_cursor.calls[0]
    for column in ("lot_id", "lot_name", "latitude", "longitude",
                   "facility_type", "facility_source"):
        assert column in sql


def test_update_parking_metadata_uses_one_parameterized_executemany():
    """型態更新必須是單一批次 executemany，lot_id 只出現在參數中。"""
    connection = SpyConnection()
    checked_at = datetime(2026, 8, 20, 4, 0, tzinfo=timezone.utc)
    updates = [{"lot_id": "TPE0001", "facility_type": "surface",
                "facility_source": "osm", "metadata_checked_at": checked_at}]

    count = database.update_parking_metadata(connection, updates)

    assert len(connection.spy_cursor.calls) == 1
    sql, values = connection.spy_cursor.calls[0]
    assert "UPDATE parking_lots" in sql
    assert "TPE0001" not in sql
    assert values == [("surface", "osm", checked_at, "TPE0001")]
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
        district_connection, district="信義區", freshness_minutes=45)
    district_sql, district_params = district_connection.spy_cursor.calls[0]
    assert "AND district = %s" in district_sql
    assert district_params == (45, "信義區")
    assert rows == [{"lot_id": "TPE0001"}]


def test_current_lots_can_filter_city_and_district():
    """城市與行政區同時存在時，條件必須一起加入且順序固定。"""
    connection = SpyConnection([])
    database.fetch_current_lots(
        connection, city="新北市", district="板橋區", freshness_minutes=45)

    sql, params = connection.spy_cursor.calls[0]
    assert "AND city = %s" in sql
    assert params == (45, "新北市", "板橋區")


def test_latest_snapshot_times_and_stale_fallback_queries():
    """每來源最新快照可讀最後時間，降級查詢則不得保留時間門檻。"""
    captured_at = datetime(2026, 8, 4, 6, 0)
    time_connection = SpyConnection([
        {"source": "taipei", "captured_at": captured_at}])
    assert database.fetch_latest_snapshot_times(time_connection) == {
        "taipei": captured_at}
    latest_sql = time_connection.spy_cursor.calls[0][0]
    assert "GROUP BY l.source" in latest_sql

    stale_connection = SpyConnection([{"lot_id": "TPE0001"}])
    rows = database.fetch_current_lots(
        stale_connection, district="信義區", freshness_minutes=None)
    sql, params = stale_connection.spy_cursor.calls[0]
    assert "UTC_TIMESTAMP()" not in sql
    assert params == ("信義區",)
    assert rows == [{"lot_id": "TPE0001"}]


def test_fetch_latest_snapshot_times_groups_by_source():
    """每來源最新快照時間必須由單一群組查詢產生，供 Task 6 分別判斷新鮮度。"""
    taipei_time = datetime(2026, 8, 4, 6, 0)
    new_taipei_time = datetime(2026, 8, 4, 5, 0)
    connection = SpyConnection([
        {"source": "taipei", "captured_at": taipei_time},
        {"source": "new_taipei", "captured_at": new_taipei_time},
    ])

    times = database.fetch_latest_snapshot_times(connection)

    assert times == {"taipei": taipei_time, "new_taipei": new_taipei_time}
    sql, _params = connection.spy_cursor.calls[0]
    assert "JOIN parking_lots l ON l.lot_id = s.lot_id" in sql
    assert "GROUP BY l.source" in sql


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

    result = collector.collect_once(new_taipei_enabled=False)

    assert [row["lot_id"] for row in saved] == ["TPE0001"]
    assert result["taipei"] == {"status": "ok", "lots": 3, "snapshots": 1}
    assert connection.committed is True
    assert connection.closed is True


def test_collect_once_rolls_back_and_closes_when_insert_fails(monkeypatch):
    """快照寫入失敗時不得 commit，且必須 rollback、close 並回報 error。"""
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

    result = collector.collect_once(new_taipei_enabled=False)

    assert result["taipei"]["status"] == "error"
    assert result["taipei"]["error"] == "RuntimeError"
    assert connection.committed is False
    assert connection.rolled_back is True
    assert connection.closed is True


def test_collect_once_commits_taipei_when_new_taipei_fails(monkeypatch):
    """新北失敗不得回滾臺北；collect_once 必須逐來源回報狀態。"""
    monkeypatch.setattr(collector, "collect_source", lambda source, timeout=15: (
        {"lots": 2, "snapshots": 2} if source == "taipei"
        else (_ for _ in ()).throw(requests.Timeout("new taipei timeout"))))

    result = collector.collect_once(new_taipei_enabled=True)

    assert result["taipei"]["status"] == "ok"
    assert result["new_taipei"]["status"] == "error"


def test_source_write_rolls_back_only_its_own_connection(monkeypatch):
    """寫入失敗只 rollback 該來源自己的連線，臺北仍獨立 commit。"""
    taipei_connection = TransactionConnection()
    new_taipei_connection = TransactionConnection()
    connections = [taipei_connection, new_taipei_connection]
    monkeypatch.setattr(collector, "get_connection", lambda: connections.pop(0))
    static = load_fixture("taipei_static.json")
    dynamic = load_fixture("taipei_dynamic.json")
    monkeypatch.setattr(collector, "fetch_json",
                        lambda url, timeout=15: static if "alldesc" in url else dynamic)
    monkeypatch.setattr(new_taipei_source, "fetch_pages",
                        lambda *_args, **_kwargs: [{"ID": "010056", "AVAILABLECAR": "24"}])
    monkeypatch.setattr(collector, "fetch_source_lot_state", lambda *_args: {
        "latest_updated_at": datetime.now(timezone.utc) - timedelta(hours=2),
        "totals": {"NTP:010056": 453}})
    monkeypatch.setattr(collector, "upsert_parking_lots", lambda conn, rows: len(rows))
    monkeypatch.setattr(collector, "insert_snapshots", lambda connection, rows: (
        (_ for _ in ()).throw(RuntimeError("insert failed"))
        if connection is new_taipei_connection else len(rows)))

    result = collector.collect_once(new_taipei_enabled=True)

    assert result["taipei"]["status"] == "ok"
    assert result["new_taipei"]["status"] == "error"
    assert taipei_connection.committed is True
    assert new_taipei_connection.rolled_back is True


def test_new_taipei_static_download_is_skipped_within_one_day(monkeypatch):
    """24 小時內且已有場站時，不得重複抓取靜態端點或改寫標記。"""
    monkeypatch.setattr(collector, "fetch_source_lot_state", lambda *_args: {
        "latest_updated_at": datetime.now(timezone.utc) - timedelta(hours=2),
        "totals": {"NTP:010056": 453}})
    static_fetch = Mock(side_effect=AssertionError("static endpoint must be skipped"))
    monkeypatch.setattr(collector, "fetch_new_taipei_static", static_fetch)
    mark_static = Mock()
    monkeypatch.setattr(collector, "update_static_fetched_at", mark_static)
    connection = TransactionConnection()
    monkeypatch.setattr(collector, "get_connection", lambda: connection)
    monkeypatch.setattr(new_taipei_source, "fetch_pages",
                        lambda *_args, **_kwargs: [{"ID": "010056", "AVAILABLECAR": "24"}])
    monkeypatch.setattr(collector, "insert_snapshots", lambda conn, rows: len(rows))

    result = collector.collect_source("new_taipei", timeout=3)

    static_fetch.assert_not_called()
    mark_static.assert_not_called()
    assert result == {"lots": 0, "snapshots": 1}
    assert connection.committed is True


def test_new_taipei_static_refetches_after_24h_and_marks_fetch(monkeypatch):
    """靜態標記超過 24 小時必須重新抓取靜態，並寫入新的抓取標記。"""
    old_marker = datetime.now(timezone.utc) - timedelta(hours=25)
    monkeypatch.setattr(collector, "fetch_source_lot_state", lambda *_args: {
        "latest_updated_at": old_marker, "totals": {"NTP:010056": 453}})
    fetched_at = datetime.now(timezone.utc)
    static_fetch = Mock(return_value=(
        [{"lot_id": "NTP:010056", "total_spaces": 453}], fetched_at))
    monkeypatch.setattr(collector, "fetch_new_taipei_static", static_fetch)
    connection = TransactionConnection()
    monkeypatch.setattr(collector, "get_connection", lambda: connection)
    monkeypatch.setattr(new_taipei_source, "fetch_pages",
                        lambda *_args, **_kwargs: [{"ID": "010056", "AVAILABLECAR": "24"}])
    monkeypatch.setattr(collector, "upsert_parking_lots", lambda conn, rows: len(rows))
    marked = []
    monkeypatch.setattr(
        collector, "update_static_fetched_at",
        lambda conn, lot_ids, when: marked.append((lot_ids, when)) or len(lot_ids))
    monkeypatch.setattr(collector, "insert_snapshots", lambda conn, rows: len(rows))

    result = collector.collect_source("new_taipei", timeout=3)

    static_fetch.assert_called_once()
    assert marked == [(["NTP:010056"], fetched_at)]
    assert result == {"lots": 1, "snapshots": 1}
    assert connection.committed is True


def test_new_taipei_static_handles_naive_db_marker_as_utc(monkeypatch):
    """DATETIME 欄位回傳的 naive 標記必須視為 UTC，不得因比較例外失敗。"""
    naive_old = (datetime.now(timezone.utc) - timedelta(hours=25)).replace(tzinfo=None)
    monkeypatch.setattr(collector, "fetch_source_lot_state", lambda *_args: {
        "latest_updated_at": naive_old, "totals": {"NTP:010056": 453}})
    static_fetch = Mock(return_value=([], None))
    monkeypatch.setattr(collector, "fetch_new_taipei_static", static_fetch)
    connection = TransactionConnection()
    monkeypatch.setattr(collector, "get_connection", lambda: connection)
    monkeypatch.setattr(new_taipei_source, "fetch_pages",
                        lambda *_args, **_kwargs: [])
    monkeypatch.setattr(collector, "insert_snapshots", lambda conn, rows: len(rows))

    collector.collect_source("new_taipei", timeout=3)

    static_fetch.assert_called_once()
    assert connection.committed is True


def test_fetch_source_lot_state_returns_totals_and_static_marker():
    """每來源必須能一次讀出場站總車位與最近一次靜態抓取標記。"""
    marker = datetime(2026, 8, 26, 12, 0)
    connection = SpyConnection([
        {"lot_id": "NTP:010056", "total_spaces": 453, "static_fetched_at": marker},
        {"lot_id": "NTP:060040", "total_spaces": 60, "static_fetched_at": None},
    ])

    state = database.fetch_source_lot_state(connection, "new_taipei")

    assert state == {
        "latest_updated_at": marker,
        "totals": {"NTP:010056": 453, "NTP:060040": 60},
    }
    sql, params = connection.spy_cursor.calls[0]
    assert "static_fetched_at" in sql
    assert params == ("new_taipei",)


def test_fetch_source_lot_state_empty_source_has_no_marker():
    """完全沒有場站時不得偽造標記，collector 必須重新抓取靜態。"""
    connection = SpyConnection([])

    assert database.fetch_source_lot_state(connection, "new_taipei") == {
        "latest_updated_at": None, "totals": {}}


def test_update_static_fetched_at_marks_only_given_lots():
    """靜態抓取標記只能以參數更新指定場站，不得拼接 ID。"""
    connection = SpyConnection()
    fetched_at = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)

    database.update_static_fetched_at(
        connection, ["NTP:010056", "NTP:060040"], fetched_at)

    sql, params = connection.spy_cursor.calls[0]
    assert "UPDATE parking_lots" in sql
    assert "static_fetched_at = %s" in sql
    assert "NTP:010056" not in sql
    assert params == (fetched_at, "NTP:010056", "NTP:060040")
