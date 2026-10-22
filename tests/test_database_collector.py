"""資料庫與蒐集器測試：驗證 SQL 參數、資料清洗及交易邊界。"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pymysql
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

    def fetchone(self):
        return self.rows[0] if self.rows else None

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


class StreamingSpyCursor(SpyCursor):
    """記錄 fetchmany 大小並依序回傳預設批次。"""

    def __init__(self, first_row=None, batches=None):
        super().__init__([first_row] if first_row else [])
        self.batches = list(batches or [])
        self.fetch_sizes = []

    def fetchmany(self, size):
        self.fetch_sizes.append(size)
        return self.batches.pop(0) if self.batches else []


class StreamingSpyConnection:
    """提供查詢與伺服器端游標測試所需的介面。"""

    def __init__(self, first_row=None, batches=None):
        self.spy_cursor = StreamingSpyCursor(first_row, batches)
        self.calls = self.spy_cursor.calls
        self.fetch_sizes = self.spy_cursor.fetch_sizes
        self.cursor_args = []

    def cursor(self, *_args):
        self.cursor_args.append(_args)
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
    assert values[0][0:4] == ("TPE0001", "測試停車場", "信義區", "市府路1號")
    assert '{"FareRule":[]}' in values[0]
    for column in ("fare_rules_json", "facility_type", "facility_source",
                   "metadata_checked_at"):
        assert column in sql
    assert count == 1


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
    """每座場站以索引查最新快照，避免快照增加後掃描整張歷史表。"""
    city_connection = SpyConnection([])
    database.fetch_current_lots(city_connection, freshness_minutes=45)
    city_sql, city_params = city_connection.spy_cursor.calls[0]
    assert "ROW_NUMBER()" not in city_sql
    assert "JOIN parking_snapshots s ON s.snapshot_id = (" in city_sql
    assert "s2.lot_id = l.lot_id" in city_sql
    assert "ORDER BY s2.captured_at DESC, s2.snapshot_id DESC" in city_sql
    # 新鮮度以官方時間判斷，不以收集時間判斷。
    assert "s2.source_updated_at >= UTC_TIMESTAMP() - INTERVAL %s MINUTE" in city_sql
    assert "s2.captured_at >= UTC_TIMESTAMP()" not in city_sql
    assert "s.source_updated_at AS snapshot_updated_at" in city_sql
    assert "AND l.district = %s" not in city_sql
    assert city_params == (45,)

    district_connection = SpyConnection([{"lot_id": "TPE0001"}])
    rows = database.fetch_current_lots(
        district_connection, "信義區", freshness_minutes=45)
    district_sql, district_params = district_connection.spy_cursor.calls[0]
    assert "AND l.district = %s" in district_sql
    assert district_params == (45, "信義區")
    assert rows == [{"lot_id": "TPE0001"}]


def test_fetch_oldest_snapshot_time_uses_captured_index_order():
    oldest = datetime(2026, 9, 1, 0, 0)
    connection = StreamingSpyConnection(first_row={"captured_at": oldest})

    assert database.fetch_oldest_snapshot_time(connection) == oldest
    sql, params = connection.calls[0]
    assert "ORDER BY captured_at LIMIT 1" in " ".join(sql.split())
    assert params is None


def test_iter_snapshot_archive_rows_streams_joined_ml_columns():
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 2, tzinfo=timezone.utc)
    rows = [{
        "lot_id": "TPE0001", "lot_name": "測試停車場", "district": "信義區",
        "total_spaces": 100, "available_spaces": 8,
        "source_updated_at": datetime(2026, 9, 1, 0, 0),
        "captured_at": datetime(2026, 9, 1, 0, 1),
    }]
    connection = StreamingSpyConnection(batches=[rows, []])

    assert list(database.iter_snapshot_archive_rows(
        connection, start, end, fetch_size=2000)) == rows
    sql, params = connection.calls[0]
    assert "JOIN parking_lots l ON l.lot_id = s.lot_id" in sql
    assert "s.captured_at >= %s AND s.captured_at < %s" in sql
    assert params == (start, end)
    assert connection.fetch_sizes == [2000, 2000]
    assert connection.cursor_args == [(pymysql.cursors.SSDictCursor,)]


def test_delete_snapshot_range_batch_is_half_open_and_bounded():
    connection = SpyConnection()
    connection.spy_cursor.rowcount = 9
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 2, tzinfo=timezone.utc)

    removed = database.delete_snapshot_range_batch(
        connection, start, end, batch_size=5000)

    sql, params = connection.spy_cursor.calls[0]
    assert "captured_at >= %s AND captured_at < %s" in " ".join(sql.split())
    assert "LIMIT %s" in sql
    assert params == (start, end, 5000)
    assert removed == 9


def test_delete_snapshot_range_batch_rejects_size_above_vm_limit():
    connection = SpyConnection()

    with pytest.raises(ValueError, match="at most 10000"):
        database.delete_snapshot_range_batch(connection, "start", "end", 10001)

    assert connection.spy_cursor.calls == []


@pytest.mark.parametrize("fetch_size", [0, -1])
def test_iter_snapshot_archive_rows_rejects_non_positive_fetch_size(fetch_size):
    connection = StreamingSpyConnection()

    with pytest.raises(ValueError, match="fetch_size must be positive"):
        list(database.iter_snapshot_archive_rows(
            connection, "start", "end", fetch_size))

    assert connection.cursor_args == []


@pytest.mark.parametrize("batch_size", [0, -1])
def test_delete_snapshot_range_batch_rejects_non_positive_batch_size(batch_size):
    connection = SpyConnection()

    with pytest.raises(ValueError, match="batch_size must be positive"):
        database.delete_snapshot_range_batch(
            connection, "start", "end", batch_size)

    assert connection.spy_cursor.calls == []


def test_latest_snapshot_and_stale_fallback_queries():
    """新鮮度安全網可讀最後時間，降級查詢則不得保留時間門檻。"""
    source_at = datetime(2026, 8, 4, 6, 0)
    time_connection = SpyConnection([{"source_updated_at": source_at}])
    assert database.fetch_latest_snapshot_time(time_connection) == source_at
    latest_sql = time_connection.spy_cursor.calls[0][0]
    assert "SELECT source_updated_at FROM parking_snapshots" in latest_sql
    assert "ORDER BY snapshot_id DESC LIMIT 1" in latest_sql
    assert "MAX(captured_at)" not in latest_sql

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
    dynamic["data"]["UPDATETIME"] = datetime.now(timezone.utc).isoformat()
    connection = TransactionConnection()
    saved = []
    monkeypatch.setattr(collector, "upsert_collector_heartbeat", lambda *_args: None)
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
    monkeypatch.setattr(collector, "upsert_collector_heartbeat", lambda *_args: None)
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


def _run_collect_once(monkeypatch, source_time, events=None):
    """以指定官方時間執行 collect_once，回傳寫入的快照與交易事件順序。"""
    static = load_fixture("taipei_static.json")
    dynamic = load_fixture("taipei_dynamic.json")
    dynamic["data"]["UPDATETIME"] = source_time.isoformat()
    events = [] if events is None else events
    saved = []
    connection = TransactionConnection()
    connection.commit = lambda: events.append("commit")
    monkeypatch.setattr(collector, "fetch_json",
                        lambda url, timeout=15: static if "alldesc" in url else dynamic)
    monkeypatch.setattr(collector, "get_connection", lambda: connection)
    monkeypatch.setattr(collector, "upsert_parking_lots", lambda conn, rows: len(rows))
    monkeypatch.setattr(collector, "insert_snapshots",
                        lambda conn, rows: saved.extend(rows) or len(rows))
    monkeypatch.setattr(collector, "upsert_collector_heartbeat",
                        lambda conn, at: events.append(("heartbeat", at)))
    result = collector.collect_once()
    return saved, events, result


def test_collect_once_skips_frozen_snapshots_older_than_retention(monkeypatch):
    """官方時間 9 天前的凍結資料會被清理，不得重新寫入成看似新收集的快照。"""
    nine_days_ago = datetime.now(timezone.utc) - timedelta(days=9)

    saved, events, result = _run_collect_once(monkeypatch, nine_days_ago)

    assert saved == []
    assert result["snapshots"] == 0
    # Collector 本身仍成功，心跳照常在 commit 前寫入。
    assert [e[0] if isinstance(e, tuple) else e for e in events] == [
        "heartbeat", "commit"]


def test_collect_once_keeps_snapshots_within_retention(monkeypatch):
    """反例：官方時間仍在保留期內（7 天前）時照常寫入。"""
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)

    saved, _events, _result = _run_collect_once(monkeypatch, seven_days_ago)

    assert [row["lot_id"] for row in saved] == ["TPE0001"]


def test_collect_once_writes_heartbeat_with_captured_time_before_commit(monkeypatch):
    now = datetime.now(timezone.utc)

    saved, events, _result = _run_collect_once(monkeypatch, now)

    assert events[-1] == "commit"
    assert events[0][0] == "heartbeat"
    assert events[0][1] == saved[0]["captured_at"]


def test_frozen_data_reinserted_after_cleanup_is_reported_stale():
    """清理清空資料表後，官方時間 9 天前、captured_at=現在的列仍必須判為過期。"""
    import query_service
    from config import Config

    now = datetime.now(timezone.utc)
    frozen_row = {
        "source_updated_at": (now - timedelta(days=9)).replace(tzinfo=None),
        "captured_at": now.replace(tzinfo=None),
    }
    connection = SpyConnection([frozen_row])

    latest = database.fetch_latest_snapshot_time(connection)
    age = query_service.snapshot_age_minutes(latest, now)

    assert age > Config.STALE_MAX_MINUTES
    # 反例：真正新的官方時間仍判為新鮮。
    fresh = query_service.snapshot_age_minutes(
        (now - timedelta(minutes=5)).replace(tzinfo=None), now)
    assert fresh <= Config.FRESHNESS_MINUTES

    current = SpyConnection([])
    database.fetch_current_lots(current, freshness_minutes=Config.STALE_MAX_MINUTES)
    sql, params = current.spy_cursor.calls[0]
    assert "s2.source_updated_at >= UTC_TIMESTAMP() - INTERVAL %s MINUTE" in sql
    assert params == (Config.STALE_MAX_MINUTES,)


class RaisingCursor(SpyCursor):
    """第一次 execute 拋出指定 MySQL 錯誤。"""

    def __init__(self, error):
        super().__init__()
        self.error = error

    def execute(self, sql, params=None):
        super().execute(sql, params)
        raise self.error


def test_upsert_collector_heartbeat_uses_single_row_parameter():
    connection = SpyConnection()
    at = datetime(2026, 10, 22, 1, 0, tzinfo=timezone.utc)

    database.upsert_collector_heartbeat(connection, at)

    sql, params = connection.spy_cursor.calls[0]
    assert "INSERT INTO collector_heartbeat (id, last_success_at)" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert params == (at,)


def test_upsert_collector_heartbeat_skips_missing_table_only():
    """舊資料庫缺表（1146）時不中斷收集；其他錯誤必須照常拋出。"""
    missing = SpyConnection()
    missing.spy_cursor = RaisingCursor(
        pymysql.err.ProgrammingError(1146, "Table doesn't exist"))
    database.upsert_collector_heartbeat(missing, datetime.now(timezone.utc))

    broken = SpyConnection()
    broken.spy_cursor = RaisingCursor(
        pymysql.err.ProgrammingError(1064, "syntax error"))
    with pytest.raises(pymysql.err.ProgrammingError):
        database.upsert_collector_heartbeat(broken, datetime.now(timezone.utc))
