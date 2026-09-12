"""每日分批刪除過期停車快照；由 cron 直接執行。"""

from datetime import datetime, timedelta, timezone

from database import delete_expired_snapshots_batch, get_connection

# 歷史圖只顯示七天，多保留一天避免時區邊界缺少資料。
SNAPSHOT_RETENTION_DAYS = 8
DELETE_BATCH_SIZE = 10000


def run_cleanup(now=None, batch_size=DELETE_BATCH_SIZE):
    """保留最近八天，逐批提交以縮短刪除期間持有鎖的時間。"""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=SNAPSHOT_RETENTION_DAYS)
    connection = get_connection()
    removed_total = 0
    try:
        while True:
            removed = delete_expired_snapshots_batch(
                connection, cutoff, batch_size)
            connection.commit()
            removed_total += removed
            if removed < batch_size:
                return removed_total
    except Exception:
        # 已提交的較早批次無須復原，只回滾目前失敗中的批次。
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    print({"deleted_snapshots": run_cleanup()})
