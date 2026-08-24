"""匿名分析事件的參數化持久層；交易由呼叫端負責。"""

from datetime import timedelta

# 資料表 16 個可寫入欄位（不含自動產生的 event_id），順序即 INSERT 順序。
EVENT_COLUMNS = (
    "event_type", "occurred_at", "request_id", "anonymous_id_hash",
    "district", "area_bucket", "place_type", "query_mode", "outcome_code",
    "duration_ms", "result_count", "clicked_rank", "parking_lot_id",
    "walking_minutes", "availability_bucket", "source",
)

# 查詢明細 26 個欄位（不含 request_id 以外的自動欄位），順序即 INSERT 順序。
QUERY_DETAIL_COLUMNS = (
    "request_id", "occurred_at", "anonymous_id_hash", "source",
    "query_mode", "raw_query_text", "parsed_query_json", "destination_label",
    "district", "arrival_time", "intent", "outcome_code", "error_stage",
    "fallback_reason", "data_status", "result_count", "location_choice_count",
    "parse_ms", "geocode_ms", "freshness_ms", "database_ms", "walking_ms",
    "total_ms", "official_data_at", "collected_at", "feedback_code",
)

# 推薦快照 18 個欄位（不含自動欄位），順序即 INSERT 順序。
RECOMMENDATION_COLUMNS = (
    "request_id", "rank_position", "occurred_at", "parking_lot_id",
    "lot_name", "recommendation_group", "available_spaces", "total_spaces",
    "pressure_label", "decision_status", "straight_distance_m",
    "walking_distance_m", "walking_minutes", "distance_source",
    "hourly_fee_label", "daily_cap_label", "facility_type_label",
    "navigation_clicked_at",
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
    """讀取時窗內查詢/地點確認事件與至 end+24h 的導航事件。"""
    select = """
        SELECT event_type, occurred_at, request_id, anonymous_id_hash,
               district, area_bucket, place_type, query_mode, outcome_code,
               duration_ms, result_count, clicked_rank, parking_lot_id,
               walking_minutes, availability_bucket, source
        FROM analytics_events
    """
    query_sql = select + (
        " WHERE event_type IN ('query_completed', 'query_failed',"
        " 'location_choice_shown', 'location_choice_selected')"
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


def upsert_query_detail(connection, detail):
    """以固定 26 欄參數化寫入查詢明細，同 request 只保留最新一筆。"""
    placeholders = ", ".join(["%s"] * len(QUERY_DETAIL_COLUMNS))
    updates = ", ".join(
        "{} = VALUES({})".format(column, column)
        for column in QUERY_DETAIL_COLUMNS
    )
    sql = (
        "INSERT INTO analytics_query_details ({columns}) VALUES ({placeholders})"
        " ON DUPLICATE KEY UPDATE {updates}"
    ).format(
        columns=", ".join(QUERY_DETAIL_COLUMNS),
        placeholders=placeholders,
        updates=updates,
    )
    params = tuple(detail[column] for column in QUERY_DETAIL_COLUMNS)
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.rowcount


def replace_recommendation_snapshots(connection, request_id, rows):
    """先刪除同 request 舊快照，再以單次批次寫入最多三筆推薦。"""
    if len(rows) > 3:
        raise ValueError("at most three recommendation snapshots per request")
    placeholders = ", ".join(["%s"] * len(RECOMMENDATION_COLUMNS))
    insert_sql = (
        "INSERT INTO analytics_recommendations ({columns}) VALUES ({placeholders})"
    ).format(
        columns=", ".join(RECOMMENDATION_COLUMNS),
        placeholders=placeholders,
    )
    params = []
    for row in rows:
        bound_row = dict(row)
        bound_row["request_id"] = request_id
        params.append(
            tuple(bound_row[column] for column in RECOMMENDATION_COLUMNS))
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM analytics_recommendations WHERE request_id = %s",
            (request_id,),
        )
        cursor.executemany(insert_sql, params)
        return cursor.rowcount


def update_query_feedback(connection, request_id, anonymous_id_hash,
                          feedback_code):
    """只更新同 request 且同裝置雜湊的明細回饋，回傳受影響列數。"""
    sql = """
        UPDATE analytics_query_details
        SET feedback_code = %s
        WHERE request_id = %s AND anonymous_id_hash = %s
    """
    params = (feedback_code, request_id, anonymous_id_hash)
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.rowcount


def fetch_insight_details(connection, start_utc, end_utc, recent_limit=20):
    """以 UTC 半開區間讀取查詢明細；recent_limit=None 時不套 LIMIT。"""
    sql = """
        SELECT {columns}
        FROM analytics_query_details
        WHERE occurred_at >= %s AND occurred_at < %s
        ORDER BY occurred_at DESC
    """.format(columns=", ".join(QUERY_DETAIL_COLUMNS))
    params = (start_utc, end_utc)
    if recent_limit is not None:
        sql += " LIMIT %s"
        params = (start_utc, end_utc, recent_limit)
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return list(cursor.fetchall())


def fetch_insight_recommendations(connection, start_utc, end_utc):
    """以 UTC 半開區間讀取推薦快照，供儀表板彙整使用。"""
    sql = """
        SELECT {columns}
        FROM analytics_recommendations
        WHERE occurred_at >= %s AND occurred_at < %s
        ORDER BY occurred_at DESC
    """.format(columns=", ".join(RECOMMENDATION_COLUMNS))
    with connection.cursor() as cursor:
        cursor.execute(sql, (start_utc, end_utc))
        return list(cursor.fetchall())


def scrub_expired_query_text(connection, cutoff_utc):
    """清空 cutoff 之前的原始文字欄位，保留其餘彙總欄位至 90 天。"""
    sql = """
        UPDATE analytics_query_details
        SET raw_query_text = NULL, parsed_query_json = NULL,
            destination_label = NULL
        WHERE occurred_at < %s
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, (cutoff_utc,))
        return cursor.rowcount


def delete_expired_insights(connection, cutoff_utc):
    """先刪除子表推薦快照，再刪除父表明細，回傳各表刪除列數。"""
    counts = {}
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM analytics_recommendations WHERE occurred_at < %s",
            (cutoff_utc,),
        )
        counts["recommendations"] = cursor.rowcount
        cursor.execute(
            "DELETE FROM analytics_query_details WHERE occurred_at < %s",
            (cutoff_utc,),
        )
        counts["query_details"] = cursor.rowcount
    return counts
