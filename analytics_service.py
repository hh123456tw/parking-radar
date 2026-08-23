"""隱私安全的匿名分析領域輔助：同意、HMAC、粗略區域與固定事件結構。"""

import hashlib
import hmac
from datetime import datetime, timezone
from math import floor
from uuid import UUID

EVENT_TYPES = frozenset({
    "query_completed", "query_failed", "navigation_clicked", "pwa_opened",
})
QUERY_EVENT_TYPES = frozenset({"query_completed", "query_failed"})
BROWSER_EVENT_TYPES = frozenset({"navigation_clicked", "pwa_opened"})
QUERY_MODES = frozenset({"manual", "chat"})
SOURCES = frozenset({"direct", "shared", "installed_pwa", "unknown"})
OUTCOME_CODES = frozenset({
    "success", "degraded_gemini_fallback", "degraded_stale_data",
    "failed_validation", "failed_geocode", "failed_no_candidates",
    "failed_database", "failed_internal",
})


def analytics_identity(headers, secret):
    """只有明確同意且 UUID 合法時，才回傳不可逆的固定 HMAC。"""
    if headers.get("X-Analytics-Consent") != "1" or not secret:
        return None
    raw_id = headers.get("X-Analytics-Id", "")
    try:
        UUID(raw_id)
    except (TypeError, ValueError, AttributeError):
        return None
    return hmac.new(secret.encode(), raw_id.encode(), hashlib.sha256).hexdigest()


def coarse_area_bucket(latitude, longitude):
    """把精確座標立即降為約一公里網格；輸出不含原始座標。"""
    if latitude is None or longitude is None:
        return None
    return f"{floor(float(latitude) * 100) / 100:.2f},{floor(float(longitude) * 100) / 100:.2f}"


def availability_bucket(spaces):
    """依現有空位數回傳固定分群，避免暴露精確空位。"""
    spaces = max(0, int(spaces))
    if spaces == 0:
        return "0"
    if spaces <= 3:
        return "1_3"
    if spaces <= 10:
        return "4_10"
    return "11_plus"


def build_query_event(
    event_type,
    request_id,
    anonymous_id_hash,
    query_mode,
    outcome_code,
    duration_ms,
    result_count,
    source,
    district=None,
    place_type=None,
    latitude=None,
    longitude=None,
):
    """以白名單欄位建構查詢事件；拒絕自由文字並回傳 UTC 時間戳。"""
    if event_type not in QUERY_EVENT_TYPES:
        raise ValueError(f"invalid event_type: {event_type}")
    return _build_event(
        event_type, request_id, anonymous_id_hash, query_mode, outcome_code,
        duration_ms, result_count, source, district, place_type,
        latitude, longitude,
    )


def build_browser_event(
    event_type,
    anonymous_id_hash,
    source,
    request_id=None,
    clicked_rank=None,
    parking_lot_id=None,
    walking_minutes=None,
    availability_bucket=None,
):
    """建構 pwa_opened/navigation_clicked 事件，只接受固定純量欄位。"""
    if event_type not in BROWSER_EVENT_TYPES:
        raise ValueError(f"invalid event_type: {event_type}")
    if source not in SOURCES:
        raise ValueError(f"invalid source: {source}")
    return _build_event(
        event_type, request_id, anonymous_id_hash, None, None, None, None,
        source, None, None, None, None,
        clicked_rank=clicked_rank,
        parking_lot_id=parking_lot_id,
        walking_minutes=walking_minutes,
        availability_bucket=availability_bucket,
    )


def _build_event(
    event_type,
    request_id,
    anonymous_id_hash,
    query_mode,
    outcome_code,
    duration_ms,
    result_count,
    source,
    district=None,
    place_type=None,
    latitude=None,
    longitude=None,
    clicked_rank=None,
    parking_lot_id=None,
    walking_minutes=None,
    availability_bucket=None,
):
    """共用事件建構：固定 16 鍵、UTC 時間戳，座標立即降為粗略網格。"""
    if query_mode is not None and query_mode not in QUERY_MODES:
        raise ValueError(f"invalid query_mode: {query_mode}")
    if outcome_code is not None and outcome_code not in OUTCOME_CODES:
        raise ValueError(f"invalid outcome_code: {outcome_code}")
    if source not in SOURCES:
        source = "unknown"
    return {
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc),
        "request_id": request_id,
        "anonymous_id_hash": anonymous_id_hash,
        "district": district,
        "area_bucket": coarse_area_bucket(latitude, longitude),
        "place_type": place_type,
        "query_mode": query_mode,
        "outcome_code": outcome_code,
        "duration_ms": duration_ms,
        "result_count": result_count,
        "clicked_rank": clicked_rank,
        "parking_lot_id": parking_lot_id,
        "walking_minutes": walking_minutes,
        "availability_bucket": availability_bucket,
        "source": source,
    }
