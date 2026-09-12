"""停車快照保留期限的一次性清理程式測試。"""

from datetime import datetime, timezone
import importlib


FIXED_NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
EXPECTED_CUTOFF = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


class FakeConnection:
    """記錄每批交易及結束狀態。"""

    def __init__(self):
        self.commits = 0
        self.rolled_back = False
        self.closed = False

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_cleanup_keeps_eight_days_and_commits_each_bounded_batch(monkeypatch):
    """滿批後必須續刪，最後不足一批即停止，並保留最近八天。"""
    snapshot_cleanup = importlib.import_module("snapshot_cleanup")
    connection = FakeConnection()
    returned_counts = iter([2, 1])
    calls = []

    monkeypatch.setattr(snapshot_cleanup, "get_connection", lambda: connection)

    def fake_delete(conn, cutoff, batch_size):
        calls.append((conn, cutoff, batch_size))
        return next(returned_counts)

    monkeypatch.setattr(
        snapshot_cleanup, "delete_expired_snapshots_batch", fake_delete)

    removed = snapshot_cleanup.run_cleanup(now=FIXED_NOW, batch_size=2)

    assert removed == 3
    assert calls == [
        (connection, EXPECTED_CUTOFF, 2),
        (connection, EXPECTED_CUTOFF, 2),
    ]
    assert connection.commits == 2
    assert connection.rolled_back is False
    assert connection.closed is True


def test_cleanup_rolls_back_current_batch_and_closes_on_failure(monkeypatch):
    """任一批失敗時回滾該批、關閉連線並保留例外。"""
    snapshot_cleanup = importlib.import_module("snapshot_cleanup")
    connection = FakeConnection()
    monkeypatch.setattr(snapshot_cleanup, "get_connection", lambda: connection)

    def fail_delete(_connection, _cutoff, _batch_size):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        snapshot_cleanup, "delete_expired_snapshots_batch", fail_delete)

    try:
        snapshot_cleanup.run_cleanup(now=FIXED_NOW, batch_size=100)
        raise AssertionError("清理失敗必須重新拋出原始例外")
    except RuntimeError as exc:
        assert str(exc) == "database unavailable"

    assert connection.commits == 0
    assert connection.rolled_back is True
    assert connection.closed is True


def test_cleanup_rejects_non_positive_batch_before_opening_database(monkeypatch):
    """批次大小為零會造成無限迴圈，必須在連線前拒絕。"""
    snapshot_cleanup = importlib.import_module("snapshot_cleanup")
    monkeypatch.setattr(
        snapshot_cleanup, "get_connection",
        lambda: (_ for _ in ()).throw(AssertionError("不應建立連線")),
    )

    try:
        snapshot_cleanup.run_cleanup(now=FIXED_NOW, batch_size=0)
        raise AssertionError("零批次必須被拒絕")
    except ValueError as exc:
        assert str(exc) == "batch_size must be positive"
