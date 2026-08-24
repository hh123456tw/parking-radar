"""分析明細與推薦快照持久層測試：固定參數順序、單次 executemany 與清理順序。"""

from datetime import datetime, timezone

import pytest

from analytics_database import (
    delete_expired_insights,
    fetch_insight_details,
    fetch_insight_recommendations,
    replace_recommendation_snapshots,
    scrub_expired_query_text,
    update_query_feedback,
    upsert_query_detail,
)

VALID_REQUEST_ID = "550e8400-e29b-41d4-a716-446655440000"
RAW_CUTOFF = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
RETENTION_CUTOFF = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
DETAIL_COLUMN_COUNT = 26
RECOMMENDATION_COLUMN_COUNT = 18


class SpyCursor:
    """記錄 execute / executemany 呼叫並提供可控列數與查詢結果。"""

    def __init__(self, rows=None, rowcount=0, rowcounts=None, executions=None):
        self.rows = rows or []
        self.rowcount = rowcount
        self.rowcounts = list(rowcounts) if rowcounts is not None else None
        self.executions = executions if executions is not None else []
        self.methods = []

    def _record(self, sql, params):
        self.executions.append((sql, params))
        if self.rowcounts:
            self.rowcount = self.rowcounts.pop(0)

    def execute(self, sql, params=None):
        self.methods.append("execute")
        self._record(sql, params)

    def executemany(self, sql, params=None):
        self.methods.append("executemany")
        self._record(sql, params)

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class SpyConnection:
    """讓 connection 與 cursor 共用同一份 SQL 執行紀錄。"""

    def __init__(self, rows=None, rowcount=0, rowcounts=None):
        self.executions = []
        self.cursor_instance = SpyCursor(
            rows, rowcount, rowcounts, self.executions)

    def cursor(self):
        return self.cursor_instance


def sample_detail(raw_query_text="今晚去台北車站"):
    """含 26 個 schema 欄位的查詢明細樣本。"""
    return {
        "request_id": VALID_REQUEST_ID,
        "occurred_at": datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc),
        "anonymous_id_hash": "a" * 64,
        "source": "direct",
        "query_mode": "manual",
        "raw_query_text": raw_query_text,
        "parsed_query_json": '{"address": "北平西路3號"}',
        "destination_label": "台北車站",
        "district": "中正區",
        "arrival_time": None,
        "intent": None,
        "outcome_code": "success",
        "error_stage": None,
        "fallback_reason": None,
        "data_status": "fresh",
        "result_count": 3,
        "location_choice_count": 0,
        "parse_ms": 10,
        "geocode_ms": 200,
        "freshness_ms": 2,
        "database_ms": 30,
        "walking_ms": 500,
        "total_ms": 742,
        "official_data_at": None,
        "collected_at": datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc),
        "feedback_code": None,
    }


def sample_recommendation(rank=1):
    """含 18 個 schema 欄位的推薦快照樣本。"""
    return {
        "request_id": VALID_REQUEST_ID,
        "rank_position": rank,
        "occurred_at": datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc),
        "parking_lot_id": "TPE000{}".format(rank),
        "lot_name": "停車場{}".format(rank),
        "recommendation_group": "recommended",
        "available_spaces": 5,
        "total_spaces": 100,
        "pressure_label": "有空位",
        "decision_status": "recommended",
        "straight_distance_m": 300,
        "walking_distance_m": 350,
        "walking_minutes": 5.0,
        "distance_source": "walking",
        "hourly_fee_label": "30 元/時",
        "daily_cap_label": "150 元/日",
        "facility_type_label": "公有",
        "navigation_clicked_at": None,
    }


def test_upsert_query_detail_binds_text_only_as_parameters():
    connection = SpyConnection(rowcount=1)
    detail = sample_detail(raw_query_text="今晚去台北車站")
    assert upsert_query_detail(connection, detail) == 1
    sql, params = connection.executions[0]
    assert "INSERT INTO analytics_query_details" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "今晚去台北車站" not in sql
    assert "今晚去台北車站" in params


def test_upsert_query_detail_uses_fixed_parameter_order():
    """26 個資料欄位必須固定順序且全部以參數傳入，不得拼接內容。"""
    connection = SpyConnection(rowcount=1)
    detail = sample_detail()
    upsert_query_detail(connection, detail)
    sql, params = connection.executions[0]
    assert sql.count("%s") == DETAIL_COLUMN_COUNT
    assert len(params) == DETAIL_COLUMN_COUNT
    assert params[0] == VALID_REQUEST_ID
    assert params[3] == "direct"
    assert params[4] == "manual"
    assert params[7] == "台北車站"
    assert params[8] == "中正區"
    assert params[22] == 742
    assert "台北車站" not in sql


def test_recommendations_replace_in_one_transaction_shape():
    connection = SpyConnection(rowcount=3)
    count = replace_recommendation_snapshots(
        connection, VALID_REQUEST_ID,
        [sample_recommendation(rank=n) for n in (1, 2, 3)])
    assert count == 3
    assert "DELETE FROM analytics_recommendations WHERE request_id = %s" \
        in connection.executions[0][0]
    assert "INSERT INTO analytics_recommendations" in connection.executions[1][0]
    assert len(connection.executions[1][1]) == 3


def test_recommendations_delete_then_single_executemany():
    """先刪除同 request 舊快照，再以一次 executemany 寫入前三名。"""
    connection = SpyConnection(rowcounts=[0, 3])
    replace_recommendation_snapshots(
        connection, VALID_REQUEST_ID,
        [sample_recommendation(rank=n) for n in (1, 2, 3)])
    assert len(connection.executions) == 2
    assert connection.cursor_instance.methods == ["execute", "executemany"]
    delete_sql, delete_params = connection.executions[0]
    insert_sql, insert_params = connection.executions[1]
    assert delete_params == (VALID_REQUEST_ID,)
    assert len(insert_params) == 3
    assert all(len(row) == RECOMMENDATION_COLUMN_COUNT for row in insert_params)
    assert insert_params[0][1] == 1
    assert insert_params[2][1] == 3
    assert insert_params[0][0] == VALID_REQUEST_ID
    assert insert_params[1][0] == VALID_REQUEST_ID
    assert insert_params[2][0] == VALID_REQUEST_ID


def test_recommendations_reject_more_than_three_rows():
    """每次查詢最多保存三筆快照，超過必須拒絕且不執行任何 SQL。"""
    connection = SpyConnection()
    rows = [sample_recommendation(rank=n) for n in (1, 2, 3, 4)]
    with pytest.raises(ValueError):
        replace_recommendation_snapshots(connection, VALID_REQUEST_ID, rows)
    assert connection.executions == []


def test_update_query_feedback_uses_exact_sql_and_parameter_order():
    """回饋只更新同 request 且同裝置雜湊的明細，順序固定。"""
    connection = SpyConnection(rowcount=1)
    count = update_query_feedback(
        connection, VALID_REQUEST_ID, "b" * 64, "found_space")
    sql, params = connection.executions[0]
    assert "UPDATE analytics_query_details" in sql
    assert "SET feedback_code = %s" in sql
    assert "WHERE request_id = %s AND anonymous_id_hash = %s" in sql
    assert params == ("found_space", VALID_REQUEST_ID, "b" * 64)
    assert count == 1


def test_fetch_insight_details_uses_bounded_window_and_limit():
    """最近查詢以半開 UTC 區間、DESC 排序並套用 LIMIT 參數。"""
    row = {"request_id": VALID_REQUEST_ID, "occurred_at": RAW_CUTOFF}
    connection = SpyConnection(rows=[row])
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 8, 24, tzinfo=timezone.utc)
    assert fetch_insight_details(connection, start, end) == [row]
    sql, params = connection.executions[0]
    assert "occurred_at >= %s AND occurred_at < %s" in sql
    assert "ORDER BY occurred_at DESC" in sql
    assert "LIMIT %s" in sql
    assert params == (start, end, 20)


def test_fetch_insight_details_can_skip_limit_for_aggregation():
    """彙整用途允許不套 LIMIT，一次取回整個時窗的全部明細。"""
    connection = SpyConnection(rows=[])
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 8, 24, tzinfo=timezone.utc)
    fetch_insight_details(connection, start, end, recent_limit=None)
    sql, params = connection.executions[0]
    assert "LIMIT" not in sql
    assert params == (start, end)


def test_fetch_insight_recommendations_never_applies_limit():
    """推薦快照讀取只以半開 UTC 區間與 DESC 排序，不套 LIMIT。"""
    connection = SpyConnection(rows=[])
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 8, 24, tzinfo=timezone.utc)
    fetch_insight_recommendations(connection, start, end)
    sql, params = connection.executions[0]
    assert "occurred_at >= %s AND occurred_at < %s" in sql
    assert "ORDER BY occurred_at DESC" in sql
    assert "LIMIT" not in sql
    assert params == (start, end)


def test_scrub_expired_query_text_nulls_text_fields():
    """14 天前的原始輸入、解析 JSON 與目的地名稱只能清成 NULL。"""
    connection = SpyConnection(rowcount=9)
    count = scrub_expired_query_text(connection, RAW_CUTOFF)
    sql, params = connection.executions[0]
    assert count == 9
    assert "SET raw_query_text = NULL" in sql
    assert "parsed_query_json = NULL" in sql
    assert "destination_label = NULL" in sql
    assert params == (RAW_CUTOFF,)
    assert "2026" not in sql


def test_cleanup_scrubs_text_before_deleting_children_and_parent():
    connection = SpyConnection()
    scrub_expired_query_text(connection, RAW_CUTOFF)
    result = delete_expired_insights(connection, RETENTION_CUTOFF)
    sql = "\n".join(call[0] for call in connection.executions)
    assert "SET raw_query_text = NULL" in sql
    assert sql.index("DELETE FROM analytics_recommendations") < \
        sql.index("DELETE FROM analytics_query_details")
    assert set(result) == {"recommendations", "query_details"}


def test_delete_expired_insights_returns_per_table_counts():
    """推薦與明細分開回傳刪除列數，且兩者都以 cutoff 為參數。"""
    connection = SpyConnection(rowcounts=[4, 6])
    result = delete_expired_insights(connection, RETENTION_CUTOFF)
    assert result == {"recommendations": 4, "query_details": 6}
    for sql, params in connection.executions:
        assert params == (RETENTION_CUTOFF,)
        assert "occurred_at < %s" in sql
        assert "2026" not in sql
