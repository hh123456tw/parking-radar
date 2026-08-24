"""每日一次性清理過期匿名分析事件；由 cron 直接執行。"""

from datetime import datetime, timedelta, timezone

from analytics_database import delete_expired_events
from config import Config
from database import get_connection


def main(now=None):
    """刪除保留期前的事件並提交；失敗時回滾後重新拋出，最後一定關閉連線。"""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=Config.ANALYTICS_RETENTION_DAYS)
    connection = get_connection()
    try:
        removed = delete_expired_events(connection, cutoff)
        connection.commit()
        return removed
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
