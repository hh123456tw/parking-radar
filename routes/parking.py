"""停車查詢與單一場站歷史；查詢邏輯在 query_service，這裡只處理 HTTP 與分析記錄。"""

import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from flask import Blueprint, current_app, jsonify, request, session

import database
import query_service
from analysis import build_history_series
from analytics_capture import build_query_detail, new_query_trace
from analytics_recorder import capture_analytics, terminal
from analytics_service import analytics_identity
from database import fetch_history

bp = Blueprint("parking", __name__)


@bp.post("/api/query")
def query_parking():
    """解析手動或聊天輸入，交由固定函式產生可驗證的停車結果。"""
    query_started = time.perf_counter()
    request_id = str(uuid4())
    anonymous_hash = analytics_identity(
        request.headers, current_app.config.get("ANALYTICS_HMAC_SECRET", ""))
    query_source = request.headers.get("X-Analytics-Source", "unknown")
    payload = request.get_json(silent=True) or {}
    query_mode = "chat" if isinstance(payload, dict) and \
        payload.get("mode") == "chat" else "manual"
    if not isinstance(payload, dict):
        return terminal({"error": "JSON 內容必須是物件"}, 400,
                        "failed_validation", query_mode, request_id,
                        anonymous_hash, query_source,
                        query_service.elapsed_ms(query_started))
    trace = new_query_trace(
        payload, query_mode, query_source, datetime.now(timezone.utc))
    outcome = query_service.run_query(
        payload, trace, query_started,
        session_state=dict(session),
        client_version=request.headers.get("X-Client-Version"),
        config=current_app.config,
        logger=current_app.logger,
    )
    duration_ms = query_service.elapsed_ms(query_started)
    if not outcome.terminal:
        # 請使用者選地點不算完成查詢：只留明細，不寫查詢事件。
        capture_analytics(
            request_id, "location_choice", "detail", build_query_detail,
            outcome.trace, request_id, anonymous_hash, outcome.outcome_code,
            duration_ms)
        return jsonify(**outcome.body, request_id=request_id)
    if outcome.session_update:
        session.update(outcome.session_update)
    return terminal(
        outcome.body, outcome.status_code, outcome.outcome_code, query_mode,
        request_id, anonymous_hash, query_source, duration_ms,
        result_count=outcome.result_count, district=outcome.district,
        latitude=outcome.latitude, longitude=outcome.longitude,
        trace=outcome.trace,
        recommendation_groups=outcome.recommendation_groups,
    )


@bp.get("/api/parking/<lot_id>/history")
def parking_history(lot_id):
    """回傳單一場站最近七天的有效空位序列供唯一折線圖使用。"""
    end_utc = datetime.now(timezone.utc)
    start_utc = end_utc - timedelta(days=7)
    connection = None
    try:
        connection = database.get_connection()
        rows = fetch_history(connection, lot_id, start_utc, end_utc)
        return jsonify(lot_id=lot_id, points=build_history_series(rows))
    except Exception:
        current_app.logger.exception("歷史查詢失敗")
        return jsonify(error="暫時無法取得歷史資料"), 503
    finally:
        if connection is not None:
            connection.close()
