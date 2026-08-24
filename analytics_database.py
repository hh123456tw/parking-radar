"""匿名分析事件的參數化持久層；交易由呼叫端負責。"""

from datetime import timedelta

# 資料表 16 個可寫入欄位（不含自動產生的 event_id），順序即 INSERT 順序。
EVENT_COLUMNS = (
    "event_type", "occurred_at", "request_id", "anonymous_id_hash",
    "district", "area_bucket", "place_type", "query_mode", "outcome_code",
    "duration_ms", "result_count", "clicked_rank", "parking_lot_id",
    "walking_minutes", "availability_bucket", "source",
)


def insert_event(connection, event):
    """以固定 16 欄參數化寫入分析事件，回傳受影響列數。"""
    placeholders = ", ".join(["%s"] * len(EVENT_COLUMNS))
    sql = (
        "INSERT INTO analytics_events ({columns}) VALUES ({placeholders})"
    ).format(columns=", ".join(EVENT_COLUMNS), placeholders=placeholders)
    params = tuple(event[column] for column in EVENT_COLUMNS)
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.rowcount


def insert_navigation_event(connection, event):
    """導航只複製 24 小時內同 hash 成功查詢的上下文，同一 request 只存第一筆。"""
    sql = """
        INSERT IGNORE INTO analytics_events
            (event_type, occurred_at, request_id, anonymous_id_hash,
             district, area_bucket, place_type, query_mode, outcome_code,
             duration_ms, result_count, clicked_rank, parking_lot_id,
             walking_minutes, availability_bucket, source)
        SELECT 'navigation_clicked', %s, q.request_id, %s,
               q.district, q.area_bucket, q.place_type, q.query_mode,
               q.outcome_code, q.duration_ms, q.result_count,
               %s, %s, %s, %s, %s
        FROM analytics_events q
        WHERE q.event_type = 'query_completed'
          AND q.anonymous_id_hash = %s
          AND q.request_id = %s
          AND q.occurred_at >= UTC_TIMESTAMP() - INTERVAL 24 HOUR
        LIMIT 1
    """
    params = (
        event["occurred_at"], event["anonymous_id_hash"],
        event["clicked_rank"], event["parking_lot_id"],
        event["walking_minutes"], event["availability_bucket"],
        event["source"], event["anonymous_id_hash"], event["request_id"],
    )
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.rowcount


def fetch_events(connection, start_utc, end_utc):
    """以 UTC 半開區間 [start, end) 讀取原始事件，供儀表板彙整。"""
    sql = """
        SELECT event_type, occurred_at, request_id, anonymous_id_hash,
               district, area_bucket, place_type, query_mode, outcome_code,
               duration_ms, result_count, clicked_rank, parking_lot_id,
               walking_minutes, availability_bucket, source
        FROM analytics_events
        WHERE occurred_at >= %s AND occurred_at < %s
        ORDER BY occurred_at
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, (start_utc, end_utc))
        return list(cursor.fetchall())


def fetch_dashboard_events(connection, start_utc, end_utc):
    """讀取時窗內查詢事件與至 end+24h 的導航事件，供儀表板彙整。"""
    select = """
        SELECT event_type, occurred_at, request_id, anonymous_id_hash,
               district, area_bucket, place_type, query_mode, outcome_code,
               duration_ms, result_count, clicked_rank, parking_lot_id,
               walking_minutes, availability_bucket, source
        FROM analytics_events
    """
    query_sql = select + (
        " WHERE event_type IN ('query_completed', 'query_failed')"
        " AND occurred_at >= %s AND occurred_at < %s ORDER BY occurred_at"
    )
    nav_sql = select + (
        " WHERE event_type = 'navigation_clicked'"
        " AND occurred_at >= %s AND occurred_at < %s ORDER BY occurred_at"
    )
    nav_end = end_utc + timedelta(hours=24)
    with connection.cursor() as cursor:
        cursor.execute(query_sql, (start_utc, end_utc))
        rows = list(cursor.fetchall())
        cursor.execute(nav_sql, (start_utc, nav_end))
        rows.extend(cursor.fetchall())
        return rows


def delete_expired_events(connection, cutoff_utc):
    """刪除 cutoff 之前的原始事件，回傳刪除列數；由 cron 排程呼叫。"""
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM analytics_events WHERE occurred_at < %s",
            (cutoff_utc,),
        )
        return cursor.rowcount


def fetch_status_times(connection):
    """一次取出儀表板需要的三個最新時間：官方資料、Collector 與後設資料。"""
    sql = """
        SELECT
            (SELECT source_updated_at FROM parking_snapshots
             ORDER BY snapshot_id DESC LIMIT 1) AS official_data_at,
            (SELECT captured_at FROM parking_snapshots
             ORDER BY snapshot_id DESC LIMIT 1) AS collector_at,
            (SELECT MAX(metadata_checked_at) FROM parking_lots) AS metadata_at
    """
    with connection.cursor() as cursor:
        cursor.execute(sql)
        rows = list(cursor.fetchall())
        row = rows[0] if rows else {}
        return {
            "official_data_at": row.get("official_data_at"),
            "collector_at": row.get("collector_at"),
            "metadata_at": row.get("metadata_at"),
        }
