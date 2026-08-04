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
            (lot_id, lot_name, district, address, operator_type,
             total_spaces, fee_info, service_time, latitude, longitude,
             supports_realtime, source_updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            lot_name=VALUES(lot_name), district=VALUES(district),
            address=VALUES(address), operator_type=VALUES(operator_type),
            total_spaces=VALUES(total_spaces), fee_info=VALUES(fee_info),
            service_time=VALUES(service_time), latitude=VALUES(latitude),
            longitude=VALUES(longitude),
            supports_realtime=VALUES(supports_realtime),
            source_updated_at=VALUES(source_updated_at)
    """
    keys = ("lot_id", "lot_name", "district", "address", "operator_type",
            "total_spaces", "fee_info", "service_time", "latitude", "longitude",
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


def fetch_current_lots(connection, district=None, freshness_minutes=45):
    """取得每個停車場 45 分鐘內最新有效快照，可選擇行政區。"""
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
            WHERE s.captured_at >= UTC_TIMESTAMP() - INTERVAL %s MINUTE
              AND l.supports_realtime = TRUE
        ) latest
        WHERE row_num = 1
    """
    params = [freshness_minutes]
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
