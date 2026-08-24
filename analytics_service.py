"""隱私安全的匿名分析領域輔助：同意、HMAC、粗略區域與固定事件結構。"""

import hashlib
import hmac
import statistics
from datetime import datetime, timedelta, timezone
from math import ceil, floor
from uuid import UUID
from zoneinfo import ZoneInfo

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
NAVIGATION_OBSERVATION_HOURS = 24
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
DASHBOARD_RANGES = frozenset({"today", "7d", "30d"})


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


def parse_dashboard_range(value, now_utc):
    """把 today/7d/30d 換算為 Asia/Taipei 午夜的 UTC 半開區間。"""
    if value not in DASHBOARD_RANGES:
        raise ValueError(f"invalid dashboard range: {value}")
    local_now = now_utc.astimezone(TAIPEI_TZ)
    local_end = local_now.replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    days = {"today": 1, "7d": 7, "30d": 30}[value]
    local_start = local_end - timedelta(days=days)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def summarize_events(rows, now_utc, min_devices=5, *, rolling_30d_rows=None):
    """彙整儀表板指標；rolling_30d_rows 提供時，重複使用率與耗時改用它。"""
    rows = [dict(row, occurred_at=_as_utc(row["occurred_at"])) for row in rows]
    query_events = [
        row for row in rows if row["event_type"] in QUERY_EVENT_TYPES
    ]
    nav_events = [
        row for row in rows if row["event_type"] == "navigation_clicked"
    ]
    # 30 天指標的資料來源：明確傳入最近 30 臺北日查詢列時優先，
    # 未傳入時退回時窗列（直接以 30d 時窗彙整仍正確）。
    rolling_rows = (
        rows if rolling_30d_rows is None else [
            dict(row, occurred_at=_as_utc(row["occurred_at"]))
            for row in rolling_30d_rows
        ]
    )
    rolling_query_events = [
        row for row in rolling_rows if row["event_type"] in QUERY_EVENT_TYPES
    ]

    completed_ids = {
        row["request_id"] for row in query_events
        if row["event_type"] == "query_completed"
    }
    failed_ids = {
        row["request_id"] for row in query_events
        if row["event_type"] == "query_failed"
    }
    completed_queries = len(completed_ids)
    total_queries = completed_queries + len(failed_ids)
    degraded_queries = len({
        row["request_id"] for row in query_events
        if row["event_type"] == "query_completed"
        and str(row.get("outcome_code") or "").startswith("degraded_")
    })

    eligible = {
        row["request_id"]: row for row in query_events
        if row["event_type"] == "query_completed"
        and (row.get("result_count") or 0) >= 1
    }
    first_clicks = {}
    for nav in sorted(nav_events, key=lambda row: row["occurred_at"]):
        query = eligible.get(nav["request_id"])
        if (
            query is None
            or nav.get("anonymous_id_hash") != query.get("anonymous_id_hash")
        ):
            continue
        click_at = nav["occurred_at"]
        observation_end = query["occurred_at"] + timedelta(
            hours=NAVIGATION_OBSERVATION_HOURS
        )
        if click_at < query["occurred_at"] or click_at >= observation_end:
            continue
        if nav["request_id"] not in first_clicks:
            first_clicks[nav["request_id"]] = nav

    click_rank_counts = {
        str(rank): sum(
            1 for nav in first_clicks.values()
            if nav.get("clicked_rank") == rank
        )
        for rank in (1, 2, 3)
    }
    click_rate = (
        len(first_clicks) / len(eligible) * 100 if eligible else None
    )
    # 暫估旗標只依「有結果的合格完成查詢」判斷；失敗或無結果查詢不會收到點擊。
    provisional = any(
        query["occurred_at"] > now_utc - timedelta(
            hours=NAVIGATION_OBSERVATION_HOURS
        )
        for query in eligible.values()
    )

    query_devices = {
        row["anonymous_id_hash"] for row in query_events
    }
    local_now = now_utc.astimezone(TAIPEI_TZ)
    local_today = local_now.replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    repeat_start_utc = (
        local_today - timedelta(days=29)
    ).astimezone(timezone.utc)
    dates_by_device = {}
    for row in rolling_query_events:
        if row["occurred_at"] < repeat_start_utc:
            continue
        dates_by_device.setdefault(
            row["anonymous_id_hash"], set()
        ).add(row["occurred_at"].astimezone(TAIPEI_TZ).date())
    repeat_devices = sum(
        1 for dates in dates_by_device.values() if len(dates) >= 2
    )
    repeat_rate = (
        repeat_devices / len(dates_by_device) * 100
        if dates_by_device else None
    )

    durations = sorted(
        row["duration_ms"] for row in rolling_query_events
        if row.get("duration_ms") is not None
    )
    median_ms = statistics.median(durations) if durations else None
    p95_ms = (
        durations[ceil(0.95 * len(durations)) - 1] if durations else None
    )

    outcome_code_counts = {}
    for row in query_events:
        code = row.get("outcome_code")
        if code:
            outcome_code_counts[code] = outcome_code_counts.get(code, 0) + 1

    return {
        "completed_queries": completed_queries,
        "query_success_rate": (
            completed_queries / total_queries * 100 if total_queries else None
        ),
        "degraded_queries": degraded_queries,
        "navigation_click_rate": click_rate,
        "navigation_provisional": provisional,
        "click_rank_counts": click_rank_counts,
        "anonymous_query_devices": len(query_devices),
        "repeat_use_rate": repeat_rate,
        "response_median_ms": median_ms,
        "response_p95_ms": p95_ms,
        "districts": _segment_rows(query_events, "district", min_devices),
        "place_types": _segment_rows(query_events, "place_type", min_devices),
        "outcome_code_counts": outcome_code_counts,
    }


def _as_utc(value):
    """資料庫回傳的無時區時間視為 UTC，統一成有時區再比較。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _segment_rows(query_events, key, min_devices):
    """依欄位值統計查詢裝置數，未達樣本下限的切片不列出。"""
    devices_by_value = {}
    for row in query_events:
        value = row.get(key)
        if value is None:
            continue
        devices_by_value.setdefault(value, set()).add(
            row["anonymous_id_hash"]
        )
    segments = [
        {key: value, "devices": len(devices)}
        for value, devices in devices_by_value.items()
        if len(devices) >= min_devices
    ]
    return sorted(segments, key=lambda item: (-item["devices"], item[key]))
