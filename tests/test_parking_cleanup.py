"""停車快照清理測試：八天截止、分批刪除、交易提交／回滾與連線關閉。"""

from datetime import datetime, timezone

import pytest

import parking_cleanup
from database import delete_old_snapshots

FIXED_NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
# 8 天前：2026-08-18T12:00Z（手算字面值，不經由被測程式推導）。
EXPECTED_CUTOFF = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


class TransactionConnection:
    """run_cleanup 使用的假連線：記錄截止參數、刪除列數或刪除失敗。"""

    def __init__(self, rowcount=0, delete_error=None):
        self.rowcount = rowcount
        self.delete_error = delete_error
        self.params = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return TransactionCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class TransactionCursor:
    """SELECT 回傳與 rowcount 同數量的 id；DELETE 回傳 rowcount 或拋出例外。"""

    def __init__(self, connection):
        self.connection = connection
        self.rowcount = 0
        self._rows = []

    def execute(self, sql, params=None):
        if params:
            self.connection.params.append(
                params[0] if isinstance(params, tuple) else params)
        if sql.lstrip().upper().startswith("SELECT"):
            self._rows = [
                {"snapshot_id": i + 1}
                for i in range(self.connection.rowcount)
            ]
            if not self._rows and self.connection.delete_error is not None:
                self._rows = [{"snapshot_id": 1}]
            self.rowcount = len(self._rows)
        else:
            if self.connection.delete_error is not None:
                raise self.connection.delete_error
            self.rowcount = self.connection.rowcount
        return self

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class BatchCursor:
    """delete_old_snapshots 使用的假 cursor：依序消耗 SELECT 批次與刪除列數。"""

    def __init__(self, connection):
        self.connection = connection
        self.rowcount = 0
        self.executions = []
        self._rows = []

    def execute(self, sql, params=None):
        self.executions.append((sql, params))
        if sql.lstrip().upper().startswith("SELECT"):
            self._rows = (
                list(self.connection.select_batches.pop(0))
                if self.connection.select_batches else [])
            self.rowcount = len(self._rows)
        else:
            self.rowcount = (
                self.connection.delete_counts.pop(0)
                if self.connection.delete_counts else 0)
        return self

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class BatchConnection:
    """提供多批 SELECT 結果與刪除列數的假連線，記錄提交次數。"""

    def __init__(self, select_batches, delete_counts):
        self.select_batches = [list(batch) for batch in select_batches]
        self.delete_counts = list(delete_counts)
        self.commits = 0
        self.cursor_instance = BatchCursor(self)

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1


def test_cleanup_uses_eight_day_cutoff_and_commits(monkeypatch):
    """run_cleanup 以 now−8 天為截止，成功時提交並關閉連線。"""
    connection = TransactionConnection(rowcount=12)
    monkeypatch.setattr(parking_cleanup, "get_connection", lambda: connection)
    assert parking_cleanup.run_cleanup(FIXED_NOW) == 12
    assert connection.params[0] == EXPECTED_CUTOFF
    assert connection.committed and connection.closed


def test_cleanup_rolls_back_and_closes_on_delete_error(monkeypatch):
    """刪除失敗時回滾未完成批次、關閉連線並重新拋出例外。"""
    connection = TransactionConnection(
        delete_error=RuntimeError("delete failed"))
    monkeypatch.setattr(parking_cleanup, "get_connection", lambda: connection)
    with pytest.raises(RuntimeError, match="delete failed"):
        parking_cleanup.run_cleanup()
    assert connection.rolled_back and connection.closed


def test_delete_old_snapshots_uses_strict_cutoff_and_primary_key_batches():
    """刪除條件必須是 captured_at < %s、依主鍵排序並以 LIMIT 分批。"""
    connection = BatchConnection(
        select_batches=[[{"snapshot_id": i} for i in range(5000)]],
        delete_counts=[5000],
    )
    assert delete_old_snapshots(connection, EXPECTED_CUTOFF) == 5000
    select_sql, select_params = connection.cursor_instance.executions[0]
    delete_sql, delete_params = connection.cursor_instance.executions[1]
    assert "captured_at < %s" in select_sql
    assert "ORDER BY snapshot_id" in select_sql
    assert "LIMIT %s" in select_sql
    assert select_params == (EXPECTED_CUTOFF, 5000)
    assert "2026" not in select_sql and "2026" not in delete_sql
    assert "snapshot_id IN (" in delete_sql
    assert len(delete_params) == 5000
    assert connection.commits == 1


def test_delete_old_snapshots_commits_each_batch_and_stops_on_short_batch():
    """每完成一批就提交，刪除少於 batch_size 時停止。"""
    connection = BatchConnection(
        select_batches=[
            [{"snapshot_id": i} for i in range(5000)],
            [{"snapshot_id": i} for i in range(5000, 5012)],
        ],
        delete_counts=[5000, 12],
    )
    assert delete_old_snapshots(connection, EXPECTED_CUTOFF) == 5012
    assert connection.commits == 2
    assert len(connection.cursor_instance.executions) == 4


def test_delete_old_snapshots_returns_zero_when_nothing_expired():
    """沒有舊快照時不執行任何 DELETE，也不提交。"""
    connection = BatchConnection(select_batches=[[]], delete_counts=[])
    assert delete_old_snapshots(connection, EXPECTED_CUTOFF) == 0
    assert connection.commits == 0
    assert len(connection.cursor_instance.executions) == 1


def test_delete_old_snapshots_accepts_custom_batch_size():
    """batch_size 必須傳入 SELECT 的 LIMIT 參數。"""
    connection = BatchConnection(
        select_batches=[[{"snapshot_id": i} for i in range(1000)]],
        delete_counts=[1000],
    )
    assert delete_old_snapshots(connection, EXPECTED_CUTOFF,
                                batch_size=1000) == 1000
    _select_sql, select_params = connection.cursor_instance.executions[0]
    assert select_params == (EXPECTED_CUTOFF, 1000)
