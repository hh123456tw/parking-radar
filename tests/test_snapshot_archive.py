import csv
import gzip
import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
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


def write_existing_archive(root, day, rows, manifest_day=None,
                           manifest_rows=None, sha256=None):
    """建立獨立手算的既有封存與完成證明。"""
    final = snapshot_archive.archive_path(root, day)
    final.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(final, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=snapshot_archive.CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    digest = sha256 or hashlib.sha256(final.read_bytes()).hexdigest()
    (final.parent / (final.name + ".manifest.json")).write_text(
        json.dumps({
            "schema_version": 1,
            "utc_day": str(manifest_day or day),
            "row_count": len(rows) if manifest_rows is None else manifest_rows,
            "sha256": digest,
        }), encoding="utf-8")
    return final


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
    manifest = json.loads(
        (expected.parent / (expected.name + ".manifest.json")).read_text(
            encoding="utf-8"))
    assert manifest == {
        "schema_version": 1,
        "utc_day": "2026-09-01",
        "row_count": 1,
        "sha256": hashlib.sha256(expected.read_bytes()).hexdigest(),
    }
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
    final = write_existing_archive(tmp_path, day, [SAMPLE_ROW])
    monkeypatch.setattr(
        snapshot_archive,
        "iter_snapshot_archive_rows",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("既有檔驗證成功後不應查資料庫")),
    )

    result = snapshot_archive.archive_day(
        object(), day, tmp_path, disk_usage=ample_disk)

    assert result == {"path": final, "rows": 1, "created": False}


@pytest.mark.parametrize("manifest_changes", [
    {"sha256": "0" * 64},
    {"manifest_day": date(2026, 9, 2)},
    {"manifest_rows": 2},
])
def test_archive_day_rejects_mismatched_existing_manifest(
        tmp_path, manifest_changes):
    final = write_existing_archive(
        tmp_path, date(2026, 9, 1), [SAMPLE_ROW], **manifest_changes)

    with pytest.raises(ValueError, match="manifest"):
        snapshot_archive.archive_day(
            object(), date(2026, 9, 1), tmp_path, disk_usage=ample_disk)

    assert final.exists()


def test_archive_day_rejects_existing_archive_without_completion_manifest(tmp_path):
    final = snapshot_archive.archive_path(tmp_path, date(2026, 9, 1))
    final.parent.mkdir(parents=True)
    with gzip.open(final, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=snapshot_archive.CSV_FIELDS)
        writer.writeheader()
        writer.writerow(SAMPLE_ROW)

    with pytest.raises(ValueError, match="manifest"):
        snapshot_archive.archive_day(
            object(), date(2026, 9, 1), tmp_path, disk_usage=ample_disk)


def test_archive_day_rejects_wrong_day_and_malformed_existing_csv(tmp_path):
    day = date(2026, 9, 1)
    final = snapshot_archive.archive_path(tmp_path, day)
    final.parent.mkdir(parents=True)
    with gzip.open(final, "wt", encoding="utf-8", newline="") as handle:
        handle.write(",".join(snapshot_archive.CSV_FIELDS) + "\n")
        handle.write("a,b,c,1,2,2026-09-01 00:00:00,not-a-time,extra\n")
    (final.parent / (final.name + ".manifest.json")).write_text(
        json.dumps({
            "schema_version": 1, "utc_day": "2026-09-01", "row_count": 1,
            "sha256": hashlib.sha256(final.read_bytes()).hexdigest(),
        }), encoding="utf-8")

    with pytest.raises(ValueError, match="CSV"):
        snapshot_archive.archive_day(
            object(), day, tmp_path, disk_usage=ample_disk)


def test_archive_day_fsyncs_file_before_no_overwrite_publication(
        tmp_path, monkeypatch):
    events = []
    original_link = os.link
    original_fsync = os.fsync
    monkeypatch.setattr(snapshot_archive, "iter_snapshot_archive_rows",
                        lambda *_args, **_kwargs: iter([SAMPLE_ROW]))
    monkeypatch.setattr(snapshot_archive.os, "fsync",
                        lambda fd: events.append("fsync") or original_fsync(fd))

    def checked_link(source, destination):
        # Each file must be flushed immediately before its create-only publish.
        assert events[-1] == "fsync"
        events.append("link")
        return original_link(source, destination)

    monkeypatch.setattr(snapshot_archive.os, "link", checked_link)

    snapshot_archive.archive_day(object(), date(2026, 9, 1), tmp_path,
                                 disk_usage=ample_disk)

    assert events.count("link") == 2
    assert events.index("link") > events.index("fsync")


def test_archive_day_fsync_failure_leaves_no_deletion_proof(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot_archive, "iter_snapshot_archive_rows",
                        lambda *_args, **_kwargs: iter([SAMPLE_ROW]))
    monkeypatch.setattr(snapshot_archive.os, "fsync",
                        lambda _fd: (_ for _ in ()).throw(OSError("fsync failed")))

    with pytest.raises(OSError, match="fsync failed"):
        snapshot_archive.archive_day(object(), date(2026, 9, 1), tmp_path,
                                     disk_usage=ample_disk)

    assert not list(tmp_path.rglob("*.csv.gz"))
    assert not list(tmp_path.rglob("*.manifest.json"))


def test_archive_day_never_overwrites_racing_final_file(tmp_path, monkeypatch):
    day = date(2026, 9, 1)
    final = snapshot_archive.archive_path(tmp_path, day)
    monkeypatch.setattr(snapshot_archive, "iter_snapshot_archive_rows",
                        lambda *_args, **_kwargs: iter([SAMPLE_ROW]))

    def race(source, destination):
        Path(destination).write_bytes(b"racing archive")
        raise FileExistsError(destination)

    monkeypatch.setattr(snapshot_archive.os, "link", race)

    with pytest.raises(FileExistsError):
        snapshot_archive.archive_day(object(), day, tmp_path, disk_usage=ample_disk)

    assert final.read_bytes() == b"racing archive"


def test_archive_day_removes_only_stale_temp_files_in_target_month(
        tmp_path, monkeypatch):
    day = date(2026, 9, 1)
    final = write_existing_archive(tmp_path, day, [SAMPLE_ROW])
    stale = final.parent / "old-random.tmp"
    stale.write_text("stale", encoding="utf-8")
    os.utime(stale, (0, 0))
    active = final.parent / "active-random.tmp"
    active.write_text("active", encoding="utf-8")
    untouched = final.parent.parent / "keep.tmp"
    untouched.write_text("keep", encoding="utf-8")

    snapshot_archive.archive_day(object(), day, tmp_path, disk_usage=ample_disk)

    assert not stale.exists()
    assert active.read_text(encoding="utf-8") == "active"
    assert untouched.read_text(encoding="utf-8") == "keep"
    assert final.exists()


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
