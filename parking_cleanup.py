"""每日清理超過 8 天的停車快照；由 systemd timer 直接執行。"""

from datetime import datetime, timedelta, timezone

from database import delete_old_snapshots, get_connection

# 圖表顯示最近 7 天；清理只刪除嚴格早於 8 天前的快照，留下一天安全餘裕。
RETENTION_DAYS = 8


def run_cleanup(now_utc: datetime | None = None) -> int:
    """以 cutoff = now − 8 天刪除舊快照，回傳刪除列數。"""
    now = now_utc or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=RETENTION_DAYS)
    connection = get_connection()
    try:
        return delete_old_snapshots(connection, cutoff)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    run_cleanup()
