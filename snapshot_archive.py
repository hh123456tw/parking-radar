import csv
import gzip
import hashlib
import json
import os
import shutil
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from database import iter_snapshot_archive_rows


CSV_FIELDS = (
    "lot_id", "lot_name", "district", "total_spaces",
    "available_spaces", "source_updated_at", "captured_at",
)
MIN_FREE_BYTES = 1024 ** 3
STALE_TEMP_SECONDS = 24 * 60 * 60


def archive_path(root, day):
    """依 UTC 日期建立可預測的年月封存路徑。"""
    root = Path(root)
    return root / f"{day:%Y}" / f"{day:%m}" / (
        f"parking-snapshots-{day:%Y-%m-%d}.csv.gz")


def _manifest_path(path):
    return Path(str(path) + ".manifest.json")


def _parse_utc(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None
            else parsed.astimezone(timezone.utc))


def _validate_csv(path, expected_day):
    """Validate cells as well as the header; DictReader otherwise hides extras."""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != CSV_FIELDS:
            raise ValueError("invalid snapshot archive CSV header")
        count = 0
        for row in reader:
            if (None in row or any(row[field] in (None, "")
                                   for field in CSV_FIELDS)):
                raise ValueError("invalid snapshot archive CSV cells")
            try:
                int(row["total_spaces"])
                int(row["available_spaces"])
                _parse_utc(row["source_updated_at"])
                captured_at = _parse_utc(row["captured_at"])
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid snapshot archive CSV cells") from exc
            if captured_at.date() != expected_day:
                raise ValueError("snapshot archive CSV captured_at outside UTC day")
            count += 1
        return count


def validate_archive(path):
    """完整讀取 gzip 與固定 CSV，回傳資料列數。"""
    name = Path(path).name
    expected_day = date.fromisoformat(name[len("parking-snapshots-"):-7])
    return _validate_csv(path, expected_day)


def _validate_complete(path, day):
    manifest_path = _manifest_path(path)
    if not manifest_path.is_file():
        raise ValueError("snapshot archive completion manifest missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid snapshot archive manifest") from exc
    expected_manifest = {"schema_version", "utc_day", "row_count", "sha256"}
    if (not isinstance(manifest, dict) or set(manifest) != expected_manifest or
            manifest.get("schema_version") != 1 or
            manifest.get("utc_day") != str(day) or
            not isinstance(manifest.get("row_count"), int) or
            isinstance(manifest.get("row_count"), bool) or
            manifest["row_count"] < 0 or
            not isinstance(manifest.get("sha256"), str)):
        raise ValueError("invalid snapshot archive manifest")
    rows = _validate_csv(path, day)
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if rows != manifest["row_count"] or digest != manifest["sha256"]:
        raise ValueError("snapshot archive manifest mismatch")
    return rows


def _fsync_file(path):
    # Windows' fsync requires a writable CRT descriptor; Linux accepts it too.
    fd = os.open(path, os.O_RDWR)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_directory(path):
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _remove_temp_files(directory):
    stale_before = time.time() - STALE_TEMP_SECONDS
    for path in directory.glob("*.tmp"):
        # A recent temp file may belong to another writer; only reap abandoned
        # files old enough that the daily job cannot still be using them.
        if path.is_file() and path.stat().st_mtime < stale_before:
            path.unlink()


def archive_day(connection, day, archive_root, disk_usage=shutil.disk_usage):
    """串流建立單日封存，完成證明與耐久化後才發布正式檔案。"""
    final_path = archive_path(archive_root, day)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    _remove_temp_files(final_path.parent)

    if disk_usage(final_path.parent).free < MIN_FREE_BYTES:
        raise RuntimeError("snapshot archive disk space below 1 GiB")
    if final_path.exists():
        # Read the archive first so a corrupt gzip is never masked by a missing
        # completion manifest.
        _validate_csv(final_path, day)
        return {"path": final_path, "rows": _validate_complete(final_path, day),
                "created": False}

    temp_path = manifest_temp = None
    manifest_path = _manifest_path(final_path)
    try:
        start_utc = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        end_utc = start_utc + timedelta(days=1)
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".tmp",
                                         dir=final_path.parent, delete=False) as temporary:
            temp_path = Path(temporary.name)
        written_rows = 0
        with gzip.open(temp_path, "wt", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for row in iter_snapshot_archive_rows(connection, start_utc, end_utc,
                                                  fetch_size=2000):
                writer.writerow(row)
                written_rows += 1
        if written_rows == 0:
            temp_path.unlink()
            temp_path = None
            return {"path": None, "rows": 0, "created": False}
        if _validate_csv(temp_path, day) != written_rows:
            raise ValueError("snapshot archive row count mismatch")
        _fsync_file(temp_path)
        os.link(temp_path, final_path)  # link gives create-only publication.
        temp_path.unlink()
        temp_path = None
        _fsync_directory(final_path.parent)

        manifest = {"schema_version": 1, "utc_day": str(day),
                    "row_count": written_rows,
                    "sha256": hashlib.sha256(final_path.read_bytes()).hexdigest()}
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".tmp",
                                         dir=final_path.parent, delete=False) as temporary:
            manifest_temp = Path(temporary.name)
            json.dump(manifest, temporary, ensure_ascii=False, separators=(",", ":"))
            temporary.flush()
            os.fsync(temporary.fileno())
        os.link(manifest_temp, manifest_path)
        manifest_temp.unlink()
        manifest_temp = None
        _fsync_directory(final_path.parent)
        return {"path": final_path, "rows": written_rows, "created": True}
    except Exception:
        for path in (temp_path, manifest_temp):
            if path is not None:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        raise
