"""隱私安全的匿名分析輔助測試：同意、HMAC、粗略區域與事件白名單。"""

from datetime import timezone

import pytest

from analytics_service import (
    analytics_identity,
    availability_bucket,
    build_query_event,
    coarse_area_bucket,
)

VALID_UUID = "550e8400-e29b-41d4-a716-446655440000"

# Task 2 資料表 16 個資料欄位（不含自動產生的 event_id），事件只能有這些鍵。
SCHEMA_KEYS = frozenset({
    "event_type", "occurred_at", "request_id", "anonymous_id_hash",
    "district", "area_bucket", "place_type", "query_mode", "outcome_code",
    "duration_ms", "result_count", "clicked_rank", "parking_lot_id",
    "walking_minutes", "availability_bucket", "source",
})


def test_identity_requires_opt_in_valid_uuid_and_secret():
    raw_id = VALID_UUID
    assert analytics_identity({}, "secret") is None
    assert analytics_identity({"X-Analytics-Consent": "1"}, "secret") is None
    headers = {"X-Analytics-Consent": "1", "X-Analytics-Id": raw_id}
    digest = analytics_identity(headers, "secret")
    assert len(digest) == 64
    assert raw_id not in digest
    assert analytics_identity(headers, "") is None


def test_identity_rejects_malformed_uuid():
    headers = {"X-Analytics-Consent": "1", "X-Analytics-Id": "not-a-uuid"}
    assert analytics_identity(headers, "secret") is None


def test_buckets_discard_exact_location_and_follow_space_boundaries():
    assert coarse_area_bucket(25.04781, 121.53191) == "25.04,121.53"
    assert coarse_area_bucket(None, 121.53191) is None
    assert [availability_bucket(value) for value in (0, 1, 3, 4, 10, 11)] == [
        "0", "1_3", "1_3", "4_10", "4_10", "11_plus",
    ]


def test_query_event_contains_only_allowlisted_fields():
    event = build_query_event(
        event_type="query_completed", request_id="req-1",
        anonymous_id_hash="a" * 64, query_mode="chat",
        outcome_code="success", duration_ms=1234, result_count=3,
        source="shared", district="中正區", latitude=25.04781,
        longitude=121.53191, place_type="station",
    )
    assert event["area_bucket"] == "25.04,121.53"
    assert "address" not in event
    assert "message" not in event


def test_query_event_returns_exact_schema_keys_and_utc_timestamp():
    event = build_query_event(
        event_type="query_failed", request_id="req-2",
        anonymous_id_hash="b" * 64, query_mode="manual",
        outcome_code="failed_validation", duration_ms=5, result_count=0,
        source="weird-source",
    )
    assert set(event) == SCHEMA_KEYS
    assert event["source"] == "unknown"
    assert event["area_bucket"] is None
    assert event["clicked_rank"] is None
    assert event["occurred_at"].tzinfo == timezone.utc


def test_query_event_rejects_invalid_enums_and_payload_kwargs():
    base = dict(
        event_type="query_completed", request_id="req-3",
        anonymous_id_hash="c" * 64, query_mode="manual",
        outcome_code="success", duration_ms=10, result_count=1,
        source="direct",
    )
    for field, bad_value in (
        ("event_type", "unknown_type"),
        ("query_mode", "voice"),
        ("outcome_code", "failed"),
    ):
        kwargs = dict(base)
        kwargs[field] = bad_value
        with pytest.raises(ValueError):
            build_query_event(**kwargs)
    with pytest.raises(TypeError):
        build_query_event(**base, message="自由文字不可進入")
