"""固定分析建構器測試：行政區推導、trace 白名單與前三名快照。"""

import json
from datetime import datetime, timezone

from analytics_database import QUERY_DETAIL_COLUMNS, RECOMMENDATION_COLUMNS
from analytics_capture import (
    PARSED_FIELDS,
    build_query_detail,
    build_recommendation_snapshots,
    infer_destination_district,
    new_query_trace,
)

REQUEST_ID = "550e8400-e29b-41d4-a716-446655440000"
NOW = datetime(2026, 8, 24, 8, 0, tzinfo=timezone.utc)


def sample_groups():
    """前三名含步行資料、第三名為備選；第四名必須被丟棄。"""
    rows = [
        {
            "lot_id": "TPE001", "lot_name": "台北車站地下停車場",
            "decision_status": "recommended", "pressure_label": "有空位",
            "available_spaces": 5, "total_spaces": 100,
            "distance_m": 300.0, "walking_distance_m": 350.0,
            "walking_duration_minutes": 5.0,
            "hourly_fee_label": "30 元/時", "daily_cap_label": "150 元/日",
            "facility_type_label": "地下停車場",
        },
        {
            "lot_id": "TPE002", "lot_name": "站前廣場停車場",
            "decision_status": "recommended", "pressure_label": "有空位",
            "available_spaces": 8, "total_spaces": 60,
            "distance_m": 450.0, "walking_distance_m": 500.0,
            "walking_duration_minutes": 8.0,
            "hourly_fee_label": "40 元/時", "daily_cap_label": "200 元/日",
            "facility_type_label": "平面式",
        },
        {
            "lot_id": "TPE003", "lot_name": "京站停車場",
            "decision_status": "warning", "pressure_label": "即將客滿",
            "available_spaces": 1, "total_spaces": 80,
            "distance_m": 600.0, "walking_distance_m": None,
            "walking_duration_minutes": None,
            "hourly_fee_label": "50 元/時", "daily_cap_label": "250 元/日",
            "facility_type_label": "立體停車場",
        },
        {
            "lot_id": "TPE004", "lot_name": "第四名停車場",
            "decision_status": "recommended", "pressure_label": "有空位",
            "available_spaces": 3, "total_spaces": 40,
            "distance_m": 700.0,
        },
    ]
    return {"recommendations": rows}


def test_district_inference_does_not_guess_from_nearest_lot():
    assert infer_destination_district(None, "臺北市中正區北平西路3號", None) == "中正區"
    assert infer_destination_district(None, "台北車站", "臺北市, 中正區, 北平西路") == "中正區"
    assert infer_destination_district("信義區", "臺北市中正區北平西路3號", None) == "信義區"
    assert infer_destination_district(None, "台北車站", "臺北市") is None


def test_district_inference_prefers_parsed_then_display_and_rejects_ambiguous():
    assert infer_destination_district(
        None, "臺北市中正區北平西路3號", "臺北市信義區市府路1號") == "中正區"
    assert infer_destination_district(None, "台北車站", "臺北市, 信義區, 市府路") == "信義區"
    assert infer_destination_district(None, "臺北市中正區信義區路口", None) is None


def test_query_detail_truncates_raw_input_and_whitelists_parsed_json():
    trace = new_query_trace({"mode": "chat", "message": "甲" * 600}, "chat", "direct", NOW)
    trace["parsed"] = {"address": "北平西路3號", "district": "中正區", "secret": "drop"}
    detail = build_query_detail(trace, REQUEST_ID, "a" * 64, "success", 1234)
    assert len(detail["raw_query_text"]) == 500
    assert "secret" not in detail["parsed_query_json"]
    assert detail["district"] == "中正區"


def test_new_query_trace_picks_raw_text_by_mode():
    chat = new_query_trace({"mode": "chat", "message": "今晚去台北車站"}, "chat", "direct", NOW)
    manual = new_query_trace({"address": "北平西路3號", "district": "信義區"}, "manual", "shared", NOW)
    district_only = new_query_trace({"district": "中正區"}, "manual", "shared", NOW)
    assert chat["raw_query_text"] == "今晚去台北車站"
    assert manual["raw_query_text"] == "北平西路3號"
    assert district_only["raw_query_text"] == "中正區"
    assert manual["query_mode"] == "manual"
    assert manual["source"] == "shared"
    assert chat["occurred_at"] == NOW


def test_query_detail_requires_valid_anonymous_hash():
    trace = new_query_trace({"mode": "chat", "message": "台北車站"}, "chat", "direct", NOW)
    for bad_hash in (None, "", "a" * 63, "a" * 65, "x" * 64, "A" * 64):
        assert build_query_detail(trace, REQUEST_ID, bad_hash, "success", 10) is None
    assert build_query_detail(trace, REQUEST_ID, "a" * 64, "success", 10) is not None


def test_query_detail_serializes_only_fixed_fields_and_datetimes_as_iso():
    trace = new_query_trace({"mode": "chat", "message": "台北車站"}, "chat", "direct", NOW)
    trace["district"] = "中正區"
    trace["parsed"] = {
        "intent": "recommend",
        "address": "臺北市中正區北平西路3號",
        "district": "中正區",
        "arrival_time": datetime(2026, 8, 24, 10, 30, tzinfo=timezone.utc),
        "destination_label": "台北車站",
        "original_destination": "drop-me",
    }
    trace["parse_ms"] = 12
    trace["data_status"] = "fresh"
    trace["result_count"] = 3
    trace["error_stage"] = None
    detail = build_query_detail(trace, REQUEST_ID, "b" * 64, "degraded_stale_data", 999)
    assert set(detail) == set(QUERY_DETAIL_COLUMNS)
    assert detail["district"] == "中正區"
    assert detail["outcome_code"] == "degraded_stale_data"
    assert detail["total_ms"] == 999
    assert detail["parse_ms"] == 12
    assert detail["result_count"] == 3
    assert detail["feedback_code"] is None
    parsed_json = json.loads(detail["parsed_query_json"])
    assert set(parsed_json) == set(PARSED_FIELDS)
    assert parsed_json["arrival_time"] == "2026-08-24T10:30:00+00:00"
    assert "original_destination" not in parsed_json
    assert "drop-me" not in detail["parsed_query_json"]


def test_recommendation_snapshots_keep_only_first_three():
    rows = build_recommendation_snapshots(REQUEST_ID, NOW, sample_groups())
    assert [row["rank_position"] for row in rows] == [1, 2, 3]
    assert rows[0]["distance_source"] == "walking"
    assert rows[2]["recommendation_group"] == "backup"


def test_recommendation_snapshot_rows_match_persistence_columns():
    rows = build_recommendation_snapshots(REQUEST_ID, NOW, sample_groups())
    assert len(rows) == 3
    for row in rows:
        assert set(row) == set(RECOMMENDATION_COLUMNS)
    assert rows[0] == {
        "request_id": REQUEST_ID, "rank_position": 1, "occurred_at": NOW,
        "parking_lot_id": "TPE001", "lot_name": "台北車站地下停車場",
        "recommendation_group": "recommended", "available_spaces": 5,
        "total_spaces": 100, "pressure_label": "有空位",
        "decision_status": "recommended", "straight_distance_m": 300,
        "walking_distance_m": 350, "walking_minutes": 5.0,
        "distance_source": "walking", "hourly_fee_label": "30 元/時",
        "daily_cap_label": "150 元/日", "facility_type_label": "地下停車場",
        "navigation_clicked_at": None,
    }


def test_recommendation_uses_straight_line_when_walking_incomplete():
    rows = build_recommendation_snapshots(REQUEST_ID, NOW, sample_groups())
    third = rows[2]
    assert third["distance_source"] == "straight_line"
    assert third["walking_distance_m"] is None
    assert third["walking_minutes"] is None
    assert third["straight_distance_m"] == 600


def test_recommendation_snapshots_accept_empty_groups():
    assert build_recommendation_snapshots(REQUEST_ID, NOW, {}) == []
    assert build_recommendation_snapshots(REQUEST_ID, NOW, {"recommendations": []}) == []
