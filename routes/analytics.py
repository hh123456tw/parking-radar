"""瀏覽器端分析事件與查詢回饋；只接受白名單欄位，失敗不影響前端。"""

from uuid import UUID

from flask import Blueprint, current_app, jsonify, request

from analytics_recorder import write_analytics_safely
from analytics_service import (
    BROWSER_EVENT_TYPES,
    SOURCES,
    analytics_identity,
    build_browser_event,
)

bp = Blueprint("analytics", __name__)


def analytics_active():
    """分析總開關開啟且有 HMAC 秘密時才接收事件。"""
    return bool(current_app.config.get("ANALYTICS_ENABLED", True)
                and current_app.config.get("ANALYTICS_HMAC_SECRET", ""))


def consented_identity(payload):
    """sendBeacon 無法帶自訂標頭；前端只在明確同意後才送出 body UUID，故以此計算 HMAC。"""
    return analytics_identity(
        {"X-Analytics-Consent": "1",
         "X-Analytics-Id": payload.get("analytics_id")},
        current_app.config.get("ANALYTICS_HMAC_SECRET", ""))


@bp.post("/api/analytics/events")
def analytics_events():
    """接受固定六種瀏覽事件與純量欄位，失敗不影響前端。"""
    if not analytics_active():
        return "", 204
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify(error="JSON 內容必須是物件"), 400
    allowed_keys = {
        "event_type", "analytics_id", "request_id", "clicked_rank",
        "parking_lot_id", "walking_minutes", "availability_bucket",
        "source",
    }
    if not allowed_keys.issuperset(payload):
        return jsonify(error="不接受未知欄位"), 400
    event_type = payload.get("event_type")
    if event_type not in BROWSER_EVENT_TYPES:
        return jsonify(error="不接受的事件類型"), 400
    source = payload.get("source")
    if source not in SOURCES:
        return jsonify(error="不接受的事件來源"), 400
    anonymous_hash = consented_identity(payload)
    if anonymous_hash is None:
        return jsonify(error="需要明確同意與合法 UUID"), 400
    event_kwargs = {
        "event_type": event_type,
        "anonymous_id_hash": anonymous_hash,
        "source": source,
    }
    # 導航維持既有必填欄位；其餘事件只接受提供的合法純量。
    required = {
        "navigation_clicked": ("request_id", "clicked_rank",
                               "parking_lot_id", "availability_bucket"),
    }
    for field in required.get(event_type, ()):
        if payload.get(field) is None:
            return jsonify(error=f"{field} 不能為空"), 400
    request_id = payload.get("request_id")
    if request_id is not None:
        try:
            UUID(request_id)
        except (TypeError, ValueError, AttributeError):
            return jsonify(error="request_id 必須是 UUID"), 400
        event_kwargs["request_id"] = request_id
    clicked_rank = payload.get("clicked_rank")
    if clicked_rank is not None and (
            not isinstance(clicked_rank, int)
            or isinstance(clicked_rank, bool)
            or not 0 <= clicked_rank <= 99):
        return jsonify(error="clicked_rank 必須是 0-99 的整數"), 400
    if clicked_rank is not None:
        event_kwargs["clicked_rank"] = clicked_rank
    parking_lot_id = payload.get("parking_lot_id")
    if parking_lot_id is not None:
        if not isinstance(parking_lot_id, str) or not parking_lot_id.strip():
            return jsonify(error="parking_lot_id 不能為空"), 400
        if len(parking_lot_id.strip()) > 32:
            return jsonify(error="parking_lot_id 不能超過 32 字元"), 400
        event_kwargs["parking_lot_id"] = parking_lot_id.strip()
    walking_minutes = payload.get("walking_minutes")
    if walking_minutes is not None and (
            isinstance(walking_minutes, bool)
            or not isinstance(walking_minutes, (int, float))
            or not 0 <= walking_minutes <= 999):
        return jsonify(error="walking_minutes 必須是非負數字"), 400
    if walking_minutes is not None:
        event_kwargs["walking_minutes"] = walking_minutes
    availability_bucket = payload.get("availability_bucket")
    if availability_bucket is not None:
        if availability_bucket not in {"0", "1_3", "4_10", "11_plus"}:
            return jsonify(error="availability_bucket 不在允許清單"), 400
        event_kwargs["availability_bucket"] = availability_bucket
    event = build_browser_event(**event_kwargs)
    write_analytics_safely(event)
    return "", 204


@bp.post("/api/analytics/feedback")
def analytics_feedback():
    """只更新同 request 同裝置的回饋碼；無匹配明細回 404，失敗不影響前端。"""
    if not analytics_active():
        return "", 204
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify(error="JSON 內容必須是物件"), 400
    allowed_keys = {"analytics_id", "request_id", "feedback_code"}
    if not allowed_keys.issuperset(payload):
        return jsonify(error="不接受未知欄位"), 400
    feedback_code = payload.get("feedback_code")
    if feedback_code not in {"found_space", "full_on_arrival", "did_not_go"}:
        return jsonify(error="feedback_code 不在允許清單"), 400
    request_id = payload.get("request_id")
    try:
        UUID(request_id)
    except (TypeError, ValueError, AttributeError):
        return jsonify(error="request_id 必須是 UUID"), 400
    anonymous_hash = consented_identity(payload)
    if anonymous_hash is None:
        return jsonify(error="需要明確同意與合法 UUID"), 400
    try:
        updated = current_app.extensions["analytics_feedback_writer"](
            anonymous_hash, request_id, feedback_code)
    except Exception:
        current_app.logger.warning(
            "analytics_feedback_write_failed request_id=%s", request_id)
        return "", 204
    if updated:
        return "", 204
    return jsonify(error="找不到對應查詢"), 404
