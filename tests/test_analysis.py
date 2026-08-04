"""停車分析純函式測試：驗證清洗、分數、距離、排行與歷史統計。"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from analysis import (
    build_history_series,
    clean_available,
    distance_ease,
    district_hell_score,
    haversine_m,
    hell_label,
    hell_score,
    rank_candidates,
    rank_district_candidates,
    recommendation_score,
    split_recommendation_groups,
    summarize_hour_comparison,
    summarize_matching_history,
)
from config import Config


def history_row(local_day, hour, available):
    """建立臺北時間樣本，再轉成資料庫使用的 UTC。"""
    local = datetime(2026, 8, local_day, hour, tzinfo=ZoneInfo("Asia/Taipei"))
    return {
        "captured_at": local.astimezone(timezone.utc),
        "total_spaces": 100,
        "available_spaces": available,
    }


def candidate(lot_id, available, latitude=25.0330, longitude=121.5654, history=None):
    """建立排名測試使用的完整候選場站。"""
    return {
        "lot_id": lot_id, "total_spaces": 100, "available_spaces": available,
        "latitude": latitude, "longitude": longitude,
        "historical_hell_score": history,
    }


def test_config_has_locked_analysis_constants():
    """搜尋半徑與新鮮度必須符合設計規格。"""
    assert Config.SEARCH_RADIUS_M == 1500
    assert Config.FRESHNESS_MINUTES == 45


def test_clean_available_rejects_special_and_impossible_values():
    """官方負數、缺值與剩餘數超過總數都不能進入計算。"""
    assert clean_available(100, -9) is None
    assert clean_available(100, None) is None
    assert clean_available(None, 10) is None
    assert clean_available(100, 101) is None
    assert clean_available(0, 0) is None
    assert clean_available(100, 20) == 20


def test_hell_score_and_label_use_fixed_thresholds():
    """地獄指數公式與四個分級邊界不可漂移。"""
    assert hell_score(200, 10) == 95.0
    assert hell_score(0, 0) is None
    assert hell_label(95.0) == "停車地獄"
    assert hell_label(80.0) == "很難停"
    assert hell_label(60.0) == "開始擠"
    assert hell_label(59.9) == "輕鬆停"


def test_district_score_is_weighted_and_handles_no_valid_rows():
    """行政區分數按車位數加權；全部無效時回傳 None。"""
    rows = [
        {"total_spaces": 100, "available_spaces": 50},
        {"total_spaces": 300, "available_spaces": 30},
        {"total_spaces": 50, "available_spaces": -9},
    ]
    assert district_hell_score(rows) == 80.0
    assert district_hell_score([]) is None


def test_haversine_and_radius_filter():
    """只保留 1.5 公里內且格數、座標有效的場站。"""
    distance = haversine_m(25.0330, 121.5654, 25.0330, 121.5704)
    assert 490 < distance < 520
    rows = [
        candidate("near", 20, longitude=121.5704),
        candidate("far", 50, longitude=121.5904),
        candidate("invalid", -9),
        candidate("no-coordinate", 50, latitude=None),
    ]
    ranked = rank_candidates(rows, 25.0330, 121.5654)
    assert [row["lot_id"] for row in ranked] == ["near"]


def test_recommendation_weights_and_distance_boundaries():
    """有無歷史使用不同權重，距離容易度限制在 0 到 100。"""
    assert recommendation_score(80, 750, 60) == 33.0
    assert recommendation_score(80, 750, None) == 32.0
    assert distance_ease(-10) == 100.0
    assert distance_ease(1500) == 0.0
    assert distance_ease(2000) == 0.0


def test_district_ranking_uses_history_and_excludes_invalid_rows():
    """行政區模式不偽造距離，並讓歷史容易度參與排序。"""
    ranked = rank_district_candidates([
        candidate("history-good", 20, history=20),
        candidate("history-bad", 20, history=90),
        candidate("invalid", -9),
    ])
    assert [row["lot_id"] for row in ranked] == ["history-good", "history-bad"]
    assert ranked[0]["distance_m"] is None


def test_split_groups_keeps_nearest_warning_and_avoid_semantics():
    """前三名推薦依分數，最近清單依距離，警告與避雷依固定門檻。"""
    ranked = [
        {"lot_id": "safe", "distance_m": 800, "hell_score": 50, "available_spaces": 50},
        {"lot_id": "warning", "distance_m": 200, "hell_score": 90, "available_spaces": 10},
        {"lot_id": "avoid", "distance_m": 300, "hell_score": 97, "available_spaces": 2},
        {"lot_id": "district", "distance_m": None, "hell_score": 40, "available_spaces": 60},
    ]
    groups = split_recommendation_groups(ranked)
    assert [row["lot_id"] for row in groups["recommendations"]] == ["safe", "warning", "avoid"]
    assert [row["lot_id"] for row in groups["nearest"]] == ["warning", "avoid", "safe"]
    assert [row["lot_id"] for row in groups["warning"]] == ["warning"]
    assert [row["lot_id"] for row in groups["avoid"]] == ["avoid"]


def test_history_requires_three_same_day_type_and_hour_samples():
    """同日別與同小時至少三筆才顯示歷史分數。"""
    arrival = datetime(2026, 8, 8, 18, tzinfo=ZoneInfo("Asia/Taipei"))
    insufficient = [history_row(1, 18, 10), history_row(2, 18, 20)]
    assert summarize_matching_history(insufficient, arrival)["hell_score"] is None
    enough = insufficient + [history_row(8, 18, 30)]
    assert summarize_matching_history(enough, arrival) == {
        "hell_score": 80.0, "sample_count": 3, "day_type": "weekend", "hour": 18}


def test_empty_history_and_weekday_weekend_comparison():
    """空歷史回傳零樣本；比較時兩組各自套用三筆門檻。"""
    arrival = datetime(2026, 8, 8, 18, tzinfo=ZoneInfo("Asia/Taipei"))
    assert summarize_matching_history([], arrival) == {
        "hell_score": None, "sample_count": 0, "day_type": "weekend", "hour": 18}
    rows = [history_row(day, 18, 50) for day in (3, 4, 5)]
    rows += [history_row(day, 18, 20) for day in (1, 2, 8)]
    comparison = summarize_hour_comparison(rows, 18)
    assert comparison["weekday"] == {"hell_score": 50.0, "sample_count": 3}
    assert comparison["weekend"] == {"hell_score": 80.0, "sample_count": 3}


def test_history_series_converts_naive_utc_and_skips_invalid_rows():
    """MySQL 無時區 UTC 要轉臺北時間，無效車位不得進圖表。"""
    rows = [
        {"captured_at": datetime(2026, 8, 3, 2, 0),
         "total_spaces": 100, "available_spaces": 10},
        {"captured_at": datetime(2026, 8, 3, 2, 30),
         "total_spaces": 100, "available_spaces": -9},
    ]
    assert build_history_series(rows) == [{
        "captured_at": "2026-08-03T10:00:00+08:00", "available_spaces": 10}]


def test_history_series_preserves_timezone_aware_instant():
    """已有時區的歷史時間只轉換顯示時區，不可重複補 UTC。"""
    point = build_history_series([history_row(1, 18, 10)])[0]
    assert point["captured_at"].endswith("18:00:00+08:00")
