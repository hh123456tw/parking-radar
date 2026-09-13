import csv
import gzip
import os
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

from database import iter_snapshot_archive_rows


CSV_FIELDS = (
    "lot_id", "lot_name", "district", "total_spaces",
    "available_spaces", "source_updated_at", "captured_at",
)
MIN_FREE_BYTES = 1024 ** 3


def archive_path(root, day):
    """依 UTC 日期建立可預測的年月封存路徑。"""
    root = Path(root)
    return root / f"{day:%Y}" / f"{day:%m}" / (
        f"parking-snapshots-{day:%Y-%m-%d}.csv.gz")


def validate_archive(path):
    """完整讀取 gzip 與固定 CSV 標頭，回傳資料列數。"""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != CSV_FIELDS:
            raise ValueError("invalid snapshot archive header")
        return sum(1 for _row in reader)


def archive_day(connection, day, archive_root, disk_usage=shutil.disk_usage):
    """串流建立單日封存，驗證完成後才以原子方式替換正式檔案。"""
    final_path = archive_path(archive_root, day)
    final_path.parent.mkdir(parents=True, exist_ok=True)

    if disk_usage(final_path.parent).free < MIN_FREE_BYTES:
        raise RuntimeError("snapshot archive disk space below 1 GiB")

    if final_path.exists():
        return {
            "path": final_path,
            "rows": validate_archive(final_path),
            "created": False,
        }

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".tmp", dir=final_path.parent,
                delete=False) as temporary:
            temp_path = Path(temporary.name)

        written_rows = 0
        with gzip.open(temp_path, "wt", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for row in iter_snapshot_archive_rows(
                    connection, day, day + timedelta(days=1), fetch_size=2000):
                writer.writerow(row)
                written_rows += 1

        if written_rows == 0:
            temp_path.unlink()
            temp_path = None
            return {"path": None, "rows": 0, "created": False}

        validated_rows = validate_archive(temp_path)
        if validated_rows != written_rows:
            raise ValueError("snapshot archive row count mismatch")
        os.replace(temp_path, final_path)
        temp_path = None
        return {"path": final_path, "rows": written_rows, "created": True}
    except Exception:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
        raise
