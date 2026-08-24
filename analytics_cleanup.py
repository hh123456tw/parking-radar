"""每日一次性清理過期匿名分析資料；由 cron 直接執行。"""

from datetime import datetime, timedelta, timezone

from analytics_database import (
    delete_expired_events,
    delete_expired_insights,
    scrub_expired_query_text,
)
from config import Config
from database import get_connection

# 原始輸入與完整目的地保留 14 天；其餘分析資料保留 90 天。
RAW_TEXT_RETENTION_DAYS = 14


def run_cleanup(now=None):
    """依序清字、刪除明細／推薦／事件並單一交易提交；失敗回滾後重拋。"""
    now = now or datetime.now(timezone.utc)
    scrub_cutoff = now - timedelta(days=RAW_TEXT_RETENTION_DAYS)
    cutoff = now - timedelta(days=Config.ANALYTICS_RETENTION_DAYS)
    connection = get_connection()
    try:
        scrubbed = scrub_expired_query_text(connection, scrub_cutoff)
        removed = delete_expired_insights(connection, cutoff)
        events = delete_expired_events(connection, cutoff)
        connection.commit()
        return {
            "scrubbed_query_text": scrubbed,
            "recommendations": removed["recommendations"],
            "query_details": removed["query_details"],
            "events": events,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    run_cleanup()
