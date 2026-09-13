import csv
import gzip
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

import snapshot_archive


SAMPLE_ROW = {
    "lot_id": "TPE0001",
    "lot_name": "臺北車站停車場",
    "district": "中正區",
    "total_spaces": 100,
    "available_spaces": 8,
    "source_updated_at": datetime(2026, 9, 1, 0, 0),
    "captured_at": datetime(2026, 9, 1, 0, 1),
}


def ample_disk(_path):
    return SimpleNamespace(free=2 * 1024 ** 3)


def test_archive_day_writes_verified_unicode_csv_gz_atomically(
        tmp_path, monkeypatch):
    day = date(2026, 9, 1)
    rows = [SAMPLE_ROW]
    monkeypatch.setattr(snapshot_archive, "iter_snapshot_archive_rows",
                        lambda *_args, **_kwargs: iter(rows))

    result = snapshot_archive.archive_day(
        object(), day, tmp_path, disk_usage=ample_disk)

    expected = tmp_path / "2026" / "09" / (
        "parking-snapshots-2026-09-01.csv.gz")
    assert snapshot_archive.archive_path(tmp_path, day) == expected
    assert result == {"path": expected, "rows": 1, "created": True}
    assert snapshot_archive.validate_archive(expected) == 1
    with gzip.open(expected, "rt", encoding="utf-8", newline="") as handle:
        archived = list(csv.DictReader(handle))
    assert archived[0]["lot_name"] == "臺北車站停車場"
    assert not list(expected.parent.glob("*.tmp"))


def test_archive_day_queries_exact_utc_midnight_bounds(
        tmp_path, monkeypatch):
    day = date(2026, 9, 1)
    captured = {}

    def capture_rows(connection, start_utc, end_utc, fetch_size=2000):
        captured.update({
            "connection": connection,
            "start_utc": start_utc,
            "end_utc": end_utc,
            "fetch_size": fetch_size,
        })
        return iter(())

    connection = object()
    monkeypatch.setattr(
        snapshot_archive, "iter_snapshot_archive_rows", capture_rows)

    result = snapshot_archive.archive_day(
        connection, day, tmp_path, disk_usage=ample_disk)

    assert result == {"path": None, "rows": 0, "created": False}
    assert captured == {
        "connection": connection,
        "start_utc": datetime(2026, 9, 1, tzinfo=timezone.utc),
        "end_utc": datetime(2026, 9, 2, tzinfo=timezone.utc),
        "fetch_size": 2000,
    }


def test_archive_day_reuses_valid_existing_archive_without_querying_database(
        tmp_path, monkeypatch):
    day = date(2026, 9, 1)
    final = snapshot_archive.archive_path(tmp_path, day)
    final.parent.mkdir(parents=True)
    with gzip.open(final, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=snapshot_archive.CSV_FIELDS)
        writer.writeheader()
        writer.writerow(SAMPLE_ROW)
    monkeypatch.setattr(
        snapshot_archive,
        "iter_snapshot_archive_rows",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("既有檔驗證成功後不應查資料庫")),
    )

    result = snapshot_archive.archive_day(
        object(), day, tmp_path, disk_usage=ample_disk)

    assert result == {"path": final, "rows": 1, "created": False}


def test_archive_day_rejects_corrupt_existing_archive_without_overwriting(
        tmp_path):
    final = snapshot_archive.archive_path(tmp_path, date(2026, 9, 1))
    final.parent.mkdir(parents=True)
    final.write_bytes(b"not-gzip")

    with pytest.raises((gzip.BadGzipFile, EOFError)):
        snapshot_archive.archive_day(
            object(), date(2026, 9, 1), tmp_path, disk_usage=ample_disk)

    assert final.read_bytes() == b"not-gzip"


def test_archive_day_low_disk_space_writes_nothing(tmp_path):
    low_disk = lambda _path: SimpleNamespace(free=1024 ** 3 - 1)

    with pytest.raises(
            RuntimeError, match="snapshot archive disk space below 1 GiB"):
        snapshot_archive.archive_day(
            object(), date(2026, 9, 1), tmp_path, disk_usage=low_disk)

    assert not list(tmp_path.rglob("*.gz"))


def test_archive_day_stream_failure_removes_temp_and_leaves_no_final(
        tmp_path, monkeypatch):
    def broken_rows(*_args, **_kwargs):
        yield SAMPLE_ROW
        raise OSError("database stream stopped")

    monkeypatch.setattr(
        snapshot_archive, "iter_snapshot_archive_rows", broken_rows)

    with pytest.raises(OSError, match="database stream stopped"):
        snapshot_archive.archive_day(
            object(), date(2026, 9, 1), tmp_path, disk_usage=ample_disk)

    assert not list(tmp_path.rglob("*.tmp"))
    assert not list(tmp_path.rglob("*.gz"))


def test_archive_day_with_no_rows_creates_no_empty_archive(
        tmp_path, monkeypatch):
    monkeypatch.setattr(
        snapshot_archive,
        "iter_snapshot_archive_rows",
        lambda *_args, **_kwargs: iter(()),
    )

    result = snapshot_archive.archive_day(
        object(), date(2026, 9, 1), tmp_path, disk_usage=ample_disk)

    assert result == {"path": None, "rows": 0, "created": False}
    assert not list(tmp_path.rglob("*.gz"))
