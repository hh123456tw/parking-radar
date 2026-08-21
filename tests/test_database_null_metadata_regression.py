"""回歸測試：資料庫遷移後的 NULL 設施來源仍可被官方資料更新。"""

import database


class SpyCursor:
    """只記錄 upsert 送出的 SQL 與參數。"""

    def __init__(self):
        self.calls = []
        self.rowcount = 0

    def executemany(self, sql, params):
        values = list(params)
        self.calls.append((sql, values))
        self.rowcount = len(values)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class SpyConnection:
    """提供 database.upsert_parking_lots 所需的最小連線介面。"""

    def __init__(self):
        self.spy_cursor = SpyCursor()

    def cursor(self):
        return self.spy_cursor


def sample_lot():
    """建立可觸發 upsert SQL 的最小停車場資料。"""
    return {
        "lot_id": "TPE0001",
        "lot_name": "測試停車場",
        "facility_type": "underground",
        "facility_source": "official",
    }


def test_official_facility_can_replace_null_existing_source():
    """CASE 條件必須把舊資料的 NULL 視為 unknown。"""
    connection = SpyConnection()

    database.upsert_parking_lots(connection, [sample_lot()])

    sql, _values = connection.spy_cursor.calls[0]
    assert sql.count("COALESCE(parking_lots.facility_source, 'unknown')") == 2
