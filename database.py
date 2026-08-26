"""集中管理 PyMySQL 連線與固定 SQL；所有外部值都以參數傳入。"""

import pymysql
from config import Config


def get_connection():
    """建立 UTF-8、DictCursor、手動交易的 MySQL 連線。"""
    return pymysql.connect(
        host=Config.MYSQL_HOST, port=Config.MYSQL_PORT,
        user=Config.MYSQL_USER, password=Config.MYSQL_PASSWORD,
        database=Config.MYSQL_DATABASE, charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, autocommit=False,
    )


def upsert_parking_lots(connection, lots):
    """以官方 lot_id 批次新增或更新基本資料，回傳受影響列數。"""
    sql = """
        INSERT INTO parking_lots
            (lot_id, city, source, source_lot_id,
             lot_name, district, address, operator_type,
             total_spaces, fee_info, fare_rules_json,
             facility_type, facility_source, metadata_checked_at,
             service_time, latitude, longitude,
             supports_realtime, source_updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            city=VALUES(city), source=VALUES(source),
            source_lot_id=VALUES(source_lot_id),
            lot_name=VALUES(lot_name), district=VALUES(district),
            address=VALUES(address), operator_type=VALUES(operator_type),
            total_spaces=VALUES(total_spaces), fee_info=VALUES(fee_info),
            -- 官方費率規則跟隨資料更新。
            fare_rules_json=VALUES(fare_rules_json),
            service_time=VALUES(service_time), latitude=VALUES(latitude),
            longitude=VALUES(longitude),
            supports_realtime=VALUES(supports_realtime),
            source_updated_at=VALUES(source_updated_at),
            -- 設施型態依優先序 manual > official > osm > unknown 保留。
            facility_type = CASE
                WHEN parking_lots.facility_source = 'manual'
                     THEN parking_lots.facility_type
                WHEN VALUES(facility_source) = 'official'
                     AND COALESCE(parking_lots.facility_source, 'unknown') IN ('osm', 'unknown')
                     THEN VALUES(facility_type)
                WHEN VALUES(facility_source) = 'manual'
                     THEN VALUES(facility_type)
                ELSE parking_lots.facility_type
            END,
            facility_source = CASE
                WHEN parking_lots.facility_source = 'manual'
                     THEN parking_lots.facility_source
                WHEN VALUES(facility_source) = 'official'
                     AND COALESCE(parking_lots.facility_source, 'unknown') IN ('osm', 'unknown')
                     THEN VALUES(facility_source)
                WHEN VALUES(facility_source) = 'manual'
                     THEN VALUES(facility_source)
                ELSE parking_lots.facility_source
            END
    """
    keys = ("lot_id", "city", "source", "source_lot_id",
            "lot_name", "district", "address", "operator_type",
            "total_spaces", "fee_info", "fare_rules_json",
            "facility_type", "facility_source", "metadata_checked_at",
            "service_time", "latitude", "longitude",
            "supports_realtime", "source_updated_at")
    values = [tuple(row.get(key) for key in keys) for row in lots]
    with connection.cursor() as cursor:
        cursor.executemany(sql, values)
        return cursor.rowcount


def insert_snapshots(connection, snapshots):
    """批次新增快照；同場站與官方時間重複時不重複累積。"""
    sql = """
        INSERT INTO parking_snapshots
            (lot_id, available_spaces, source_updated_at, captured_at)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE lot_id = VALUES(lot_id)
    """
    values = [(
        row["lot_id"], row["available_spaces"],
        row["source_updated_at"], row["captured_at"],
    ) for row in snapshots]
    with connection.cursor() as cursor:
        cursor.executemany(sql, values)
        return cursor.rowcount


def fetch_latest_snapshot_time(connection):
    """回傳資料庫最後一次成功收集時間；完全沒有快照時回傳 None。"""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT captured_at FROM parking_snapshots "
            "ORDER BY snapshot_id DESC LIMIT 1"
        )
        rows = list(cursor.fetchall())
        row = rows[0] if rows else None
        return row.get("captured_at") if row else None


def fetch_latest_snapshot_times(connection):
    """回傳每來源最後一次成功收集時間，供新鮮度分別判斷。"""
    sql = """
        SELECT l.source, MAX(s.captured_at) AS captured_at
        FROM parking_snapshots s
        JOIN parking_lots l ON l.lot_id = s.lot_id
        GROUP BY l.source
    """
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return {row["source"]: row["captured_at"] for row in cursor.fetchall()}


def fetch_latest_snapshot_time(connection):
    """暫時包裝 fetch_latest_snapshot_times：回傳全部來源的最大值。"""
    times = fetch_latest_snapshot_times(connection)
    return max(times.values()) if times else None


def fetch_source_lot_state(connection, source):
    """回傳來源目前場站總車位與最近一次成功靜態抓取的標記時間。"""
    sql = """
        SELECT lot_id, total_spaces, static_fetched_at
        FROM parking_lots
        WHERE source = %s
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, (source,))
        rows = list(cursor.fetchall())
    fetched = [row["static_fetched_at"] for row in rows
               if row.get("static_fetched_at") is not None]
    return {
        "latest_updated_at": max(fetched) if fetched else None,
        "totals": {row["lot_id"]: row["total_spaces"] for row in rows},
    }


def update_static_fetched_at(connection, lot_ids, fetched_at):
    """只由靜態資料抓取成功後呼叫；動態-only 週期不得改寫此標記。"""
    if not lot_ids:
        return 0
    placeholders = ",".join(["%s"] * len(lot_ids))
    sql = f"""
        UPDATE parking_lots
        SET static_fetched_at = %s
        WHERE lot_id IN ({placeholders})
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, (fetched_at,) + tuple(lot_ids))
        return cursor.rowcount


def fetch_current_lots(connection, city=None, district=None,
                       freshness_minutes=45):
    """取得每場站最新有效快照；freshness_minutes=None 時允許舊資料。"""
    freshness_sql = ""
    params = []
    if freshness_minutes is not None:
        freshness_sql = (
            "AND s.captured_at >= UTC_TIMESTAMP() - INTERVAL %s MINUTE"
        )
        params.append(freshness_minutes)
    sql = """
        SELECT * FROM (
            SELECT l.*, s.available_spaces,
                   s.source_updated_at AS snapshot_updated_at,
                   s.captured_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY l.lot_id ORDER BY s.captured_at DESC
                   ) AS row_num
            FROM parking_lots l
            JOIN parking_snapshots s ON s.lot_id = l.lot_id
            WHERE l.supports_realtime = TRUE
              {freshness_sql}
        ) latest
        WHERE row_num = 1
    """.format(freshness_sql=freshness_sql)
    if city:
        sql += " AND city = %s"
        params.append(city)
    if district:
        sql += " AND district = %s"
        params.append(district)
    with connection.cursor() as cursor:
        cursor.execute(sql, tuple(params))
        return list(cursor.fetchall())


def fetch_history(connection, lot_id, start_utc, end_utc):
    """取得單一停車場指定 UTC 區間的格數及總格數。"""
    sql = """
        SELECT s.lot_id, s.available_spaces, s.captured_at, l.total_spaces
        FROM parking_snapshots s
        JOIN parking_lots l ON l.lot_id = s.lot_id
        WHERE s.lot_id = %s AND s.captured_at BETWEEN %s AND %s
        ORDER BY s.captured_at
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, (lot_id, start_utc, end_utc))
        return list(cursor.fetchall())


def fetch_matching_history(connection, lot_ids, start_utc, end_utc):
    """一次取得候選場站歷史，避免每個場站各查一次造成 N+1。"""
    if not lot_ids:
        return []
    placeholders = ",".join(["%s"] * len(lot_ids))
    sql = f"""
        SELECT s.lot_id, s.available_spaces, s.captured_at, l.total_spaces
        FROM parking_snapshots s
        JOIN parking_lots l ON l.lot_id = s.lot_id
        WHERE s.lot_id IN ({placeholders})
          AND s.captured_at BETWEEN %s AND %s
        ORDER BY s.lot_id, s.captured_at
    """
    params = tuple(lot_ids) + (start_utc, end_utc)
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return list(cursor.fetchall())


def get_cached_geocode(connection, normalized_address):
    """依主鍵查詢地址快取；不存在時回傳 None。"""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT * FROM geocode_cache WHERE normalized_address = %s",
            (normalized_address,),
        )
        rows = list(cursor.fetchall())
        return rows[0] if rows else None


def save_cached_geocode(connection, result):
    """新增或更新地址快取，呼叫端負責 commit 或 rollback。"""
    sql = """
        INSERT INTO geocode_cache
            (normalized_address, display_address, latitude, longitude, cached_at)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE display_address=VALUES(display_address),
            latitude=VALUES(latitude), longitude=VALUES(longitude),
            cached_at=VALUES(cached_at)
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, (
            result["normalized_address"], result["display_address"],
            result["latitude"], result["longitude"], result["cached_at"],
        ))


def fetch_parking_metadata_candidates(connection):
    """取出型態同步所需的場站候選：含座標與現有型態來源。"""
    sql = """
        SELECT lot_id, lot_name, latitude, longitude,
               facility_type, facility_source
        FROM parking_lots
    """
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return list(cursor.fetchall())


def update_parking_metadata(connection, updates):
    """以單一參數化批次寫入型態與檢查時間，lot_id 只經由參數傳入。"""
    sql = """
        UPDATE parking_lots
        SET facility_type = %s, facility_source = %s, metadata_checked_at = %s
        WHERE lot_id = %s
    """
    values = [(
        row["facility_type"], row["facility_source"],
        row["metadata_checked_at"], row["lot_id"],
    ) for row in updates]
    with connection.cursor() as cursor:
        cursor.executemany(sql, values)
        return cursor.rowcount
