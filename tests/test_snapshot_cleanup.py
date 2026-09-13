"""每日快照清理的 UTC 日期與封存先行契約測試。"""

from datetime import date, datetime, timezone
import importlib

import pytest


FIXED_NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


class FakeConnection:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def test_complete_expired_days_keeps_partial_cutoff_day():
    snapshot_cleanup = importlib.import_module("snapshot_cleanup")
    oldest = datetime(2026, 9, 1, 2, 0, tzinfo=timezone.utc)
    now = datetime(2026, 9, 10, 19, 23, tzinfo=timezone.utc)

    assert snapshot_cleanup.complete_expired_days(oldest, now) == [
        date(2026, 9, 1),
    ]


def test_complete_expired_days_handles_empty_database():
    snapshot_cleanup = importlib.import_module("snapshot_cleanup")

    assert snapshot_cleanup.complete_expired_days(None, FIXED_NOW) == []


def test_complete_expired_days_normalizes_naive_mysql_time():
    snapshot_cleanup = importlib.import_module("snapshot_cleanup")
    oldest = datetime(2026, 9, 1, 23, 0)
    now = datetime(2026, 9, 10, 19, 23)

    assert snapshot_cleanup.complete_expired_days(oldest, now) == [
        date(2026, 9, 1),
    ]


def test_cleanup_archives_each_day_before_deleting_it(monkeypatch, tmp_path):
    snapshot_cleanup = importlib.import_module("snapshot_cleanup")
    connection = FakeConnection()
    events = []
    monkeypatch.setattr(snapshot_cleanup, "get_connection", lambda: connection)
    monkeypatch.setattr(
        snapshot_cleanup, "fetch_oldest_snapshot_time",
        lambda _connection: datetime(2026, 9, 1, 0, 0),
    )

    def fake_archive(_connection, day, _root):
        events.append(("archive", day))
        return {"rows": 2, "created": True}

    counts = iter([2, 0])

    def fake_delete(_connection, start, end, batch_size):
        events.append(("delete", start, end, batch_size))
        return next(counts)

    monkeypatch.setattr(snapshot_cleanup, "archive_day", fake_archive)
    monkeypatch.setattr(snapshot_cleanup, "delete_snapshot_range_batch", fake_delete)

    result = snapshot_cleanup.run_cleanup(
        now=datetime(2026, 9, 10, 19, 23, tzinfo=timezone.utc),
        batch_size=2, archive_root=tmp_path)

    assert events[0] == ("archive", date(2026, 9, 1))
    assert events[1][0] == "delete"
    assert result == {
        "archived_files": 1,
        "archived_rows": 2,
        "deleted_snapshots": 2,
    }
    assert connection.commits == 2


def test_cleanup_reuses_valid_existing_archive_before_deleting(monkeypatch, tmp_path):
    snapshot_cleanup = importlib.import_module("snapshot_cleanup")
    connection = FakeConnection()
    monkeypatch.setattr(snapshot_cleanup, "get_connection", lambda: connection)
    monkeypatch.setattr(
        snapshot_cleanup, "fetch_oldest_snapshot_time",
        lambda _connection: datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        snapshot_cleanup, "archive_day",
        lambda *_args: {"rows": 1, "created": False},
    )
    monkeypatch.setattr(
        snapshot_cleanup, "delete_snapshot_range_batch", lambda *_args: 0)

    assert snapshot_cleanup.run_cleanup(
        now=datetime(2026, 9, 10, 19, 23, tzinfo=timezone.utc),
        archive_root=tmp_path) == {
            "archived_files": 1, "archived_rows": 1, "deleted_snapshots": 0,
        }


def test_cleanup_archive_failure_never_calls_delete(monkeypatch, tmp_path):
    snapshot_cleanup = importlib.import_module("snapshot_cleanup")
    connection = FakeConnection()
    monkeypatch.setattr(snapshot_cleanup, "get_connection", lambda: connection)
    monkeypatch.setattr(
        snapshot_cleanup, "fetch_oldest_snapshot_time",
        lambda _connection: datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        snapshot_cleanup, "archive_day",
        lambda *_args: (_ for _ in ()).throw(OSError("archive failed")),
    )
    monkeypatch.setattr(
        snapshot_cleanup, "delete_snapshot_range_batch",
        lambda *_args: (_ for _ in ()).throw(AssertionError("delete called")),
    )

    with pytest.raises(OSError, match="archive failed"):
        snapshot_cleanup.run_cleanup(
            now=datetime(2026, 9, 10, 19, 23, tzinfo=timezone.utc),
            archive_root=tmp_path)

    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closed is True


def test_cleanup_delete_failure_rolls_back_and_reraises(monkeypatch, tmp_path):
    snapshot_cleanup = importlib.import_module("snapshot_cleanup")
    connection = FakeConnection()
    monkeypatch.setattr(snapshot_cleanup, "get_connection", lambda: connection)
    monkeypatch.setattr(
        snapshot_cleanup, "fetch_oldest_snapshot_time",
        lambda _connection: datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        snapshot_cleanup, "archive_day", lambda *_args: {"rows": 1, "created": True})
    monkeypatch.setattr(
        snapshot_cleanup, "delete_snapshot_range_batch",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("delete failed")),
    )

    with pytest.raises(RuntimeError, match="delete failed"):
        snapshot_cleanup.run_cleanup(
            now=datetime(2026, 9, 10, 19, 23, tzinfo=timezone.utc),
            archive_root=tmp_path)

    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closed is True


def test_cleanup_empty_database_returns_zeroes(monkeypatch, tmp_path):
    snapshot_cleanup = importlib.import_module("snapshot_cleanup")
    connection = FakeConnection()
    monkeypatch.setattr(snapshot_cleanup, "get_connection", lambda: connection)
    monkeypatch.setattr(snapshot_cleanup, "fetch_oldest_snapshot_time", lambda _: None)

    assert snapshot_cleanup.run_cleanup(now=FIXED_NOW, archive_root=tmp_path) == {
        "archived_files": 0, "archived_rows": 0, "deleted_snapshots": 0,
    }


def test_cleanup_rejects_invalid_batch_before_opening_database(monkeypatch):
    snapshot_cleanup = importlib.import_module("snapshot_cleanup")
    monkeypatch.setattr(
        snapshot_cleanup, "get_connection",
        lambda: (_ for _ in ()).throw(AssertionError("connection opened")),
    )

    with pytest.raises(ValueError, match="positive"):
        snapshot_cleanup.run_cleanup(now=FIXED_NOW, batch_size=0)
    with pytest.raises(ValueError, match="10000"):
        snapshot_cleanup.run_cleanup(now=FIXED_NOW, batch_size=10001)
