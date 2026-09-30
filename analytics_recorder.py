"""分析寫入：一律最佳努力，任何失敗只留警告，不得影響查詢回應。"""

from flask import current_app, jsonify

import database
from analytics_capture import build_query_detail, build_recommendation_snapshots
from analytics_database import (
    insert_event,
    insert_navigation_event,
    replace_recommendation_snapshots,
    update_query_feedback,
    upsert_query_detail,
)
from analytics_service import build_query_event


def run_analytics_write(operation, *args):
    """共用短交易：成功提交並回傳列數，失敗回滾並重拋，最後關閉連線。"""
    connection = None
    try:
        connection = database.get_connection()
        result = operation(connection, *args)
        connection.commit()
        return result
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if connection is not None:
            connection.close()


def analytics_writer(event):
    """以短交易寫入單一事件，成功才提交。"""
    def write_event(connection, event):
        if event["event_type"] == "navigation_clicked":
            insert_navigation_event(connection, event)
        else:
            insert_event(connection, event)
    run_analytics_write(write_event, event)


def analytics_detail_writer(detail):
    run_analytics_write(upsert_query_detail, detail)


def analytics_recommendation_writer(rows):
    """獨立短交易替換最多三筆推薦快照，成功才提交。"""
    if rows:
        run_analytics_write(
            replace_recommendation_snapshots, rows[0]["request_id"], rows)


def analytics_feedback_writer(anonymous_id_hash, request_id, feedback_code):
    """短交易更新同 request 同裝置的回饋碼，回傳受影響列數。"""
    return run_analytics_write(
        update_query_feedback, request_id, anonymous_id_hash, feedback_code)


def register_writers(app):
    """寫入函式放在 app.extensions，測試可逐一替換而不必碰資料庫。"""
    app.extensions["analytics_writer"] = analytics_writer
    app.extensions["analytics_detail_writer"] = analytics_detail_writer
    app.extensions["analytics_recommendation_writer"] = \
        analytics_recommendation_writer
    app.extensions["analytics_feedback_writer"] = analytics_feedback_writer


def write_analytics_safely(event):
    """分析寫入失敗只能留下不含目的地的警告，不得影響查詢。"""
    if not event:
        return
    try:
        current_app.extensions["analytics_writer"](event)
    except Exception:
        current_app.logger.warning(
            "analytics_write_failed event=%s",
            event.get("event_type", "unknown"))


def record_query_event(outcome_code, query_mode, request_id,
                       anonymous_hash, query_source, duration_ms,
                       result_count=0, district=None, latitude=None,
                       longitude=None):
    """在最佳努力隔離內建構並寫入查詢事件，任何異常都不外洩。"""
    if not anonymous_hash:
        return
    try:
        event = build_query_event(
            event_type="query_completed"
            if outcome_code.startswith("success")
            or outcome_code.startswith("degraded")
            else "query_failed",
            request_id=request_id,
            anonymous_id_hash=anonymous_hash,
            query_mode=query_mode,
            outcome_code=outcome_code,
            duration_ms=duration_ms,
            result_count=result_count,
            source=query_source,
            district=district,
            latitude=latitude,
            longitude=longitude,
        )
    except Exception:
        current_app.logger.warning("analytics_event_build_failed")
        return
    write_analytics_safely(event)


def capture_analytics(request_id, stage, label, builder, *args):
    """最佳努力寫入分析列；失敗只留 request_id 與 stage 警告。"""
    try:
        rows = builder(*args)
        if rows:
            current_app.extensions[f"analytics_{label}_writer"](rows)
    except Exception:
        current_app.logger.warning(
            "analytics_%s_write_failed request_id=%s stage=%s",
            label, request_id, stage)


def terminal(payload, status_code, outcome_code, query_mode, request_id,
             anonymous_hash, query_source, duration_ms, result_count=0,
             district=None, latitude=None, longitude=None, trace=None,
             recommendation_groups=None):
    """加上 request_id 回傳終端 JSON，並最佳努力記錄事件、明細與快照。"""
    payload["request_id"] = request_id
    record_query_event(
        outcome_code, query_mode, request_id, anonymous_hash,
        query_source, duration_ms, result_count, district, latitude,
        longitude)
    if trace is not None and anonymous_hash:
        capture_analytics(
            request_id, "terminal", "detail", build_query_detail,
            trace, request_id, anonymous_hash, outcome_code, duration_ms)
        if recommendation_groups is not None:
            capture_analytics(
                request_id, "terminal", "recommendation",
                build_recommendation_snapshots, request_id,
                trace["occurred_at"], recommendation_groups or {})
    return jsonify(payload), status_code
