"""唯讀管理介面；Nginx Basic Auth 負責保護所有 /admin/ 路徑。"""

from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, render_template, request

import database
from analytics_database import (
    fetch_dashboard_events,
    fetch_events,
    fetch_insight_details,
    fetch_insight_recommendations,
)
from analytics_service import (
    DASHBOARD_RANGES,
    parse_dashboard_range,
    summarize_events,
    summarize_insights,
)
from status_service import build_status

bp = Blueprint("admin", __name__, url_prefix="/admin")


def analytics_configured():
    """開關與 HMAC 秘密都設定時，儀表板才讀取分析資料。"""
    return bool(current_app.config.get("ANALYTICS_ENABLED")
                and current_app.config.get("ANALYTICS_HMAC_SECRET"))


@bp.get("/analytics")
def admin_analytics_page():
    """顯示唯讀管理儀表板。"""
    return render_template("admin_analytics.html")


@bp.get("/api/analytics")
def admin_analytics_api():
    """依 today/7d/30d 回傳彙整指標；未設定秘密時回傳誠實的空資料。"""
    range_value = request.args.get("range", "today")
    if range_value not in DASHBOARD_RANGES:
        return jsonify(error="range 只接受 today、7d、30d"), 400
    now_utc = datetime.now(timezone.utc)
    enabled = analytics_configured()
    rows, rolling_rows = [], []
    details, recommendations = [], []
    if enabled:
        connection = database.get_connection()
        try:
            start, end = parse_dashboard_range(range_value, now_utc)
            rows = fetch_dashboard_events(connection, start, end)
            rolling_start = parse_dashboard_range("30d", now_utc)[0]
            rolling_rows = fetch_events(connection, rolling_start, now_utc)
            details = fetch_insight_details(
                connection, start, end, recent_limit=None)
            recommendations = fetch_insight_recommendations(
                connection, start, end)
        except Exception:
            current_app.logger.exception("管理儀表板分析讀取失敗")
            return jsonify(error="暫時無法取得分析資料"), 503
        finally:
            connection.close()
    min_devices = current_app.config.get("ANALYTICS_SEGMENT_MIN_DEVICES", 5)
    summary = summarize_events(
        rows, now_utc, min_devices=min_devices, rolling_30d_rows=rolling_rows)
    insights = summarize_insights(
        details, recommendations, rows, min_devices=min_devices)
    return jsonify(range=range_value, analytics_enabled=enabled,
                   summary=summary, insights=insights)


@bp.get("/api/status")
def admin_status_api():
    """回傳唯讀系統狀態；各元件獨立降級且不做任何外部請求。"""
    connection = None
    try:
        connection = database.get_connection()
    except Exception:
        current_app.logger.warning("管理儀表板無法連線 MySQL")
    try:
        body = build_status(
            connection,
            deploy_version=current_app.config.get("DEPLOY_VERSION", "unknown"),
            analytics_enabled=analytics_configured(),
        )
    finally:
        if connection is not None:
            connection.close()
    return jsonify(body)
