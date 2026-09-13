"""每日快照清理的 UTC 日期與封存先行契約測試。"""

from datetime import date, datetime, timezone
import importlib
import logging

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


def test_cleanup_lock_contention_happens_before_opening_database(
        monkeypatch, tmp_path):
    snapshot_cleanup = importlib.import_module("snapshot_cleanup")

    class BusyFcntl:
        LOCK_EX = 1
        LOCK_NB = 2
        LOCK_UN = 8

        @staticmethod
        def flock(_fd, _operation):
            raise BlockingIOError("busy")

    monkeypatch.setattr(snapshot_cleanup, "fcntl", BusyFcntl)
    monkeypatch.setattr(
        snapshot_cleanup, "get_connection",
        lambda: (_ for _ in ()).throw(AssertionError("connection opened")),
    )

    with pytest.raises(RuntimeError, match="already running"):
        snapshot_cleanup.run_cleanup(now=FIXED_NOW, archive_root=tmp_path)


def test_cleanup_logs_each_deleted_day(monkeypatch, tmp_path, caplog):
    snapshot_cleanup = importlib.import_module("snapshot_cleanup")
    connection = FakeConnection()
    archive = tmp_path / "2026" / "09" / "archive.csv.gz"
    monkeypatch.setattr(snapshot_cleanup, "get_connection", lambda: connection)
    monkeypatch.setattr(
        snapshot_cleanup, "fetch_oldest_snapshot_time",
        lambda _: datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        snapshot_cleanup, "archive_day",
        lambda *_args: {"path": archive, "rows": 2, "created": True},
    )
    monkeypatch.setattr(snapshot_cleanup, "delete_snapshot_range_batch",
                        lambda *_args: 1)

    with caplog.at_level(logging.INFO, logger="snapshot_cleanup"):
        snapshot_cleanup.run_cleanup(
            now=datetime(2026, 9, 10, 19, 23, tzinfo=timezone.utc),
            archive_root=tmp_path)

    assert "utc_day=2026-09-01" in caplog.text
    assert "archive=" + str(archive) in caplog.text
    assert "archived_rows=2" in caplog.text
    assert "deleted_rows=1" in caplog.text


def test_cleanup_processes_all_batches_of_each_day_before_next_archive(
        monkeypatch, tmp_path):
    snapshot_cleanup = importlib.import_module("snapshot_cleanup")
    connection = FakeConnection()
    events = []
    monkeypatch.setattr(snapshot_cleanup, "get_connection", lambda: connection)
    monkeypatch.setattr(
        snapshot_cleanup, "fetch_oldest_snapshot_time",
        lambda _: datetime(2026, 9, 1, 0, 0),
    )

    def fake_archive(_, day, root):
        events.append(("archive", day, root))
        return {"rows": 3, "created": day.day == 1}

    counts = iter([2, 1, 2, 0])

    def fake_delete(_, start, end, batch_size):
        events.append(("delete", start, end, batch_size))
        return next(counts)

    monkeypatch.setattr(snapshot_cleanup, "archive_day", fake_archive)
    monkeypatch.setattr(snapshot_cleanup, "delete_snapshot_range_batch", fake_delete)

    assert snapshot_cleanup.run_cleanup(
        now=datetime(2026, 9, 11, 19, 23, tzinfo=timezone.utc),
        batch_size=2, archive_root=tmp_path) == {
            "archived_files": 2, "archived_rows": 6, "deleted_snapshots": 5,
        }
    assert events == [
        ("archive", date(2026, 9, 1), tmp_path),
        ("delete", datetime(2026, 9, 1, tzinfo=timezone.utc),
         datetime(2026, 9, 2, tzinfo=timezone.utc), 2),
        ("delete", datetime(2026, 9, 1, tzinfo=timezone.utc),
         datetime(2026, 9, 2, tzinfo=timezone.utc), 2),
        ("archive", date(2026, 9, 2), tmp_path),
        ("delete", datetime(2026, 9, 2, tzinfo=timezone.utc),
         datetime(2026, 9, 3, tzinfo=timezone.utc), 2),
        ("delete", datetime(2026, 9, 2, tzinfo=timezone.utc),
         datetime(2026, 9, 3, tzinfo=timezone.utc), 2),
    ]


def test_cleanup_later_archive_failure_does_not_delete_that_day(
        monkeypatch, tmp_path):
    snapshot_cleanup = importlib.import_module("snapshot_cleanup")
    connection = FakeConnection()
    deleted_days = []
    monkeypatch.setattr(snapshot_cleanup, "get_connection", lambda: connection)
    monkeypatch.setattr(
        snapshot_cleanup, "fetch_oldest_snapshot_time",
        lambda _: datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    def fake_archive(_, day, _root):
        if day == date(2026, 9, 2):
            raise OSError("later archive failed")
        return {"rows": 1, "created": True}

    monkeypatch.setattr(snapshot_cleanup, "archive_day", fake_archive)
    monkeypatch.setattr(
        snapshot_cleanup, "delete_snapshot_range_batch",
        lambda _, start, *_args: deleted_days.append(start.date()) or 0,
    )

    with pytest.raises(OSError, match="later archive failed"):
        snapshot_cleanup.run_cleanup(
            now=datetime(2026, 9, 11, 19, 23, tzinfo=timezone.utc),
            archive_root=tmp_path)

    assert deleted_days == [date(2026, 9, 1)]


def test_cleanup_preserves_falsy_archive_root(monkeypatch):
    snapshot_cleanup = importlib.import_module("snapshot_cleanup")
    connection = FakeConnection()
    roots = []
    monkeypatch.setattr(snapshot_cleanup, "get_connection", lambda: connection)
    monkeypatch.setattr(
        snapshot_cleanup, "fetch_oldest_snapshot_time",
        lambda _: datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        snapshot_cleanup, "archive_day",
        lambda _, _day, root: roots.append(root) or {"rows": 0, "created": False},
    )

    snapshot_cleanup.run_cleanup(
        now=datetime(2026, 9, 10, 19, 23, tzinfo=timezone.utc), archive_root="")

    assert roots == [""]


def test_main_prints_cleanup_result_directly(monkeypatch, capsys):
    snapshot_cleanup = importlib.import_module("snapshot_cleanup")
    expected = {"archived_files": 1, "archived_rows": 2,
                "deleted_snapshots": 2}
    monkeypatch.setattr(snapshot_cleanup, "run_cleanup", lambda: expected)

    snapshot_cleanup.main()

    assert capsys.readouterr().out == f"{expected}\n"
