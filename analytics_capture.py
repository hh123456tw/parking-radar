"""固定、可測試的分析建構器：行政區推導、查詢 trace、明細與推薦快照。"""

import json
from datetime import datetime, timezone

from ai_service import TAIPEI_DISTRICTS

RAW_TEXT_LIMIT = 500
ANONYMOUS_HASH_LENGTH = 64
_HEX = frozenset("0123456789abcdef")

# 只能進入 parsed_query_json 的固定結構化欄位；模型其他欄位一律丟棄。
PARSED_FIELDS = ("intent", "address", "district", "arrival_time", "destination_label")


def infer_destination_district(explicit, parsed_address, display_address):
    """只依明確行政區與地址文字推導；無法確認時回傳 None，絕不猜測。"""
    if explicit in TAIPEI_DISTRICTS:
        return explicit
    for text in (parsed_address, display_address):
        matches = [d for d in TAIPEI_DISTRICTS if d in (text or "")]
        if len(matches) == 1:
            return matches[0]
    return None


def new_query_trace(payload, query_mode, source, occurred_at):
    """建立固定 trace；原始文字依模式取 chat message／manual address／district。"""
    if query_mode == "chat":
        raw_text = payload.get("message")
    else:
        raw_text = payload.get("address") or payload.get("district")
    return {
        "query_mode": query_mode,
        "source": source,
        "occurred_at": _as_utc(occurred_at),
        "raw_query_text": _truncate(raw_text),
        "parsed": {},
        "district": None,
        "parse_ms": None,
        "geocode_ms": None,
        "freshness_ms": None,
        "database_ms": None,
        "walking_ms": None,
        "error_stage": None,
        "fallback_reason": None,
        "data_status": None,
        "result_count": 0,
        "location_choice_count": 0,
        "official_data_at": None,
        "collected_at": None,
    }


def build_query_detail(trace, request_id, anonymous_id_hash, outcome_code, total_ms):
    """把 trace 轉成固定 26 欄查詢明細；匿名雜湊不合法時回傳 None。"""
    if not _valid_anonymous_hash(anonymous_id_hash):
        return None
    parsed = trace.get("parsed") or {}
    return {
        "request_id": request_id,
        "occurred_at": _as_utc(trace["occurred_at"]),
        "anonymous_id_hash": anonymous_id_hash,
        "source": trace["source"],
        "query_mode": trace["query_mode"],
        "raw_query_text": _truncate(trace["raw_query_text"]),
        "parsed_query_json": json.dumps(
            {field: _iso_value(parsed.get(field)) for field in PARSED_FIELDS},
            ensure_ascii=False, sort_keys=True, default=str),
        "destination_label": parsed.get("destination_label"),
        "district": trace.get("district") or parsed.get("district"),
        "arrival_time": _as_utc(parsed.get("arrival_time")),
        "intent": parsed.get("intent"),
        "outcome_code": outcome_code,
        "error_stage": trace.get("error_stage"),
        "fallback_reason": trace.get("fallback_reason"),
        "data_status": trace.get("data_status"),
        "result_count": trace.get("result_count", 0),
        "location_choice_count": trace.get("location_choice_count", 0),
        "parse_ms": trace.get("parse_ms"),
        "geocode_ms": trace.get("geocode_ms"),
        "freshness_ms": trace.get("freshness_ms"),
        "database_ms": trace.get("database_ms"),
        "walking_ms": trace.get("walking_ms"),
        "total_ms": total_ms,
        "official_data_at": trace.get("official_data_at"),
        "collected_at": trace.get("collected_at"),
        "feedback_code": None,
    }


def build_recommendation_snapshots(request_id, occurred_at, groups):
    """只取 recommendations 前三名並轉成固定 18 欄快照。"""
    rows = []
    for rank, row in enumerate(
            (groups.get("recommendations") or [])[:3], start=1):
        has_walking = (
            row.get("walking_distance_m") is not None
            and row.get("walking_duration_minutes") is not None
        )
        rows.append({
            "request_id": request_id,
            "rank_position": rank,
            "occurred_at": _as_utc(occurred_at),
            "parking_lot_id": row.get("lot_id"),
            "lot_name": row.get("lot_name"),
            "recommendation_group": "recommended"
                if row.get("decision_status") == "recommended" else "backup",
            "available_spaces": row.get("available_spaces"),
            "total_spaces": row.get("total_spaces"),
            "pressure_label": row.get("pressure_label"),
            "decision_status": row.get("decision_status"),
            "straight_distance_m": _meters(row.get("distance_m")),
            "walking_distance_m": _meters(row.get("walking_distance_m"))
                if has_walking else None,
            "walking_minutes": float(row["walking_duration_minutes"])
                if has_walking else None,
            "distance_source": "walking" if has_walking else "straight_line",
            "hourly_fee_label": row.get("hourly_fee_label"),
            "daily_cap_label": row.get("daily_cap_label"),
            "facility_type_label": row.get("facility_type_label"),
            "navigation_clicked_at": None,
        })
    return rows


def _valid_anonymous_hash(value):
    """HMAC hexdigest 固定 64 字元小寫十六進位；None 或格式錯誤都拒絕。"""
    return (
        isinstance(value, str)
        and len(value) == ANONYMOUS_HASH_LENGTH
        and all(char in _HEX for char in value)
    )


def _truncate(value):
    """原始輸入截到固定 500 字元；None 保持 None。"""
    return None if value is None else str(value)[:RAW_TEXT_LIMIT]


def _iso_value(value):
    """datetime 轉 ISO 字串，其餘值原樣保留給 JSON 序列化。"""
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _as_utc(value):
    """無時區視為 UTC；有時區轉成 UTC；非 datetime 原樣保留。"""
    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _meters(value):
    """距離統一成整數公尺，符合 schema 的 INT 欄位。"""
    return None if value is None else int(round(float(value)))
