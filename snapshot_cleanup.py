"""每日封存後分批刪除過期停車快照；由 cron 直接執行。"""

from datetime import date, datetime, time, timedelta, timezone

from snapshot_archive import archive_day
from config import SNAPSHOT_ARCHIVE_DIR
from database import (
    delete_snapshot_range_batch,
    fetch_oldest_snapshot_time,
    get_connection,
)

# 歷史圖只顯示七天，多保留一天避免時區邊界缺少資料。
SNAPSHOT_RETENTION_DAYS = 8
DELETE_BATCH_SIZE = 10000


def iter_days(start, stop):
    """逐日產生半開區間內的日期。"""
    while start < stop:
        yield start
        start += timedelta(days=1)


def _utc_datetime(value):
    """將 MySQL 無時區時間視為 UTC，並統一有時區時間。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def complete_expired_days(oldest, now, retention_days=SNAPSHOT_RETENTION_DAYS):
    """列出截止日以前已完整結束的 UTC 日期。"""
    if oldest is None:
        return []
    boundary_day = (_utc_datetime(now) - timedelta(days=retention_days)).date()
    day = _utc_datetime(oldest).date()
    return list(iter_days(day, boundary_day))


def run_cleanup(now=None, batch_size=DELETE_BATCH_SIZE, archive_root=None):
    """逐日先完成封存，再以受限批次刪除快照。"""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if batch_size > DELETE_BATCH_SIZE:
        raise ValueError("batch_size must be at most 10000")
    now = now or datetime.now(timezone.utc)
    if archive_root is None:
        archive_root = SNAPSHOT_ARCHIVE_DIR
    connection = get_connection()
    result = {"archived_files": 0, "archived_rows": 0,
              "deleted_snapshots": 0}
    try:
        oldest = fetch_oldest_snapshot_time(connection)
        for day in complete_expired_days(oldest, now):
            archive = archive_day(connection, day, archive_root)
            if archive["rows"] == 0:
                continue
            result["archived_files"] += 1
            result["archived_rows"] += archive["rows"]
            start = datetime.combine(day, time.min, tzinfo=timezone.utc)
            end = start + timedelta(days=1)
            while True:
                removed = delete_snapshot_range_batch(
                    connection, start, end, batch_size)
                connection.commit()
                result["deleted_snapshots"] += removed
                if removed < batch_size:
                    break
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main():
    """執行清理並直接輸出完整結果。"""
    print(run_cleanup())


if __name__ == "__main__":
    main()
