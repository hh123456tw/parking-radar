"""小型車費率解讀測試：結構化 FareInfo 與官方 payex 文字皆為純字串邏輯。"""

import json
from datetime import datetime

from fee_service import build_fee_summary


def rules(*rows):
    return json.dumps({"FareRule": list(rows)}, ensure_ascii=False)


def day(iso="2026-08-19T18:00:00+08:00"):
    return datetime.fromisoformat(iso)


def averaged_result(**overrides):
    expected = {
        "hourly_fee_label": "官方未標示",
        "hourly_fee_value": None,
        "daily_cap_label": "官方未標示",
        "fee_note": None,
        "fee_confidence": "unknown",
    }
    expected.update(overrides)
    return expected


def test_selects_small_car_hourly_rule_for_arrival_time():
    result = build_fee_summary(rules(
        {"ParkingType": "C", "RateType": "1", "ChargeableSTime": "0800",
         "ChargeableETime": "2200", "ParkingRates": "60"},
        {"ParkingType": "M", "RateType": "1", "ChargeableSTime": "0000",
         "ChargeableETime": "2400", "ParkingRates": "20"},
    ), "", datetime.fromisoformat("2026-08-19T18:00:00+08:00"), "weekday")

    assert result == {
        "hourly_fee_label": "60 元／時",
        "hourly_fee_value": 60,
        "daily_cap_label": "官方未標示",
        "fee_note": None,
        "fee_confidence": "exact",
    }


def test_accepts_cm_parking_type_rule():
    """CM（小型車乙類）視同小型車，抵達時段適用即取用。"""
    result = build_fee_summary(rules(
        {"ParkingType": "CM", "RateType": "1", "ChargeableSTime": "0800",
         "ChargeableETime": "2200", "ParkingRates": "50"},
    ), "", day(), "weekday")

    assert result["hourly_fee_label"] == "50 元／時"
    assert result["fee_confidence"] == "exact"


def test_matches_cross_midnight_rule_at_night():
    """2200–0800 跨午夜規則在凌晨 02:00 抵達時應適用。"""
    result = build_fee_summary(rules(
        {"ParkingType": "C", "RateType": "1", "ChargeableSTime": "2200",
         "ChargeableETime": "0800", "ParkingRates": "40"},
    ), "", datetime.fromisoformat("2026-08-19T02:00:00+08:00"), "weekday")

    assert result == averaged_result(
        hourly_fee_label="40 元／時", hourly_fee_value=40,
        fee_confidence="exact")


def test_cross_midnight_rule_ignored_outside_window():
    """18:00 不在 2200–0800 區間，不得誤用夜間費率。"""
    result = build_fee_summary(rules(
        {"ParkingType": "C", "RateType": "1", "ChargeableSTime": "2200",
         "ChargeableETime": "0800", "ParkingRates": "40"},
    ), "", day(), "weekday")

    assert result["hourly_fee_label"] == "官方未標示"
    assert result["fee_confidence"] == "unknown"


def test_handles_2400_end_marker_as_all_day():
    """0000–2400 視為全天適用，任何抵達時間皆命中。"""
    result = build_fee_summary(rules(
        {"ParkingType": "C", "RateType": "1", "ChargeableSTime": "0000",
         "ChargeableETime": "2400", "ParkingRates": "30"},
    ), "", day(), "weekday")

    assert result["hourly_fee_label"] == "30 元／時"
    assert result["fee_confidence"] == "exact"


def test_ignores_rate_types_other_than_hourly():
    """RateType 2（計次）與 3（月租）不得作為每小時費率。"""
    result = build_fee_summary(rules(
        {"ParkingType": "C", "RateType": "2", "ChargeableSTime": "0000",
         "ChargeableETime": "2400", "ParkingRates": "100"},
        {"ParkingType": "C", "RateType": "3", "ChargeableSTime": "0000",
         "ChargeableETime": "2400", "ParkingRates": "3000"},
    ), "", day(), "weekday")

    assert result["hourly_fee_label"] == "官方未標示"
    assert result["fee_confidence"] == "unknown"


def test_returns_range_for_multiple_applicable_prices():
    """同一抵達時段有多個合理費率時，回傳最小到最大區間而非臆測。"""
    result = build_fee_summary(rules(
        {"ParkingType": "C", "RateType": "1", "ChargeableSTime": "0800",
         "ChargeableETime": "2200", "ParkingRates": "40"},
        {"ParkingType": "C", "RateType": "1", "ChargeableSTime": "0800",
         "ChargeableETime": "2200", "ParkingRates": "60"},
    ), "", day(), "weekday")

    assert result["hourly_fee_label"] == "40～60 元／時"
    assert result["hourly_fee_value"] is None
    assert result["fee_confidence"] == "range"


def test_deduplicates_identical_prices():
    """重複的相同費率去重複後仍應回傳單一確定金額。"""
    result = build_fee_summary(rules(
        {"ParkingType": "C", "RateType": "1", "ChargeableSTime": "0800",
         "ChargeableETime": "2200", "ParkingRates": "60"},
        {"ParkingType": "C", "RateType": "1", "ChargeableSTime": "0800",
         "ChargeableETime": "2200", "ParkingRates": "60"},
    ), "", day(), "weekday")

    assert result == averaged_result(
        hourly_fee_label="60 元／時", hourly_fee_value=60,
        fee_confidence="exact")


def test_malformed_json_returns_unknown_never_raises():
    """格式錯誤的 FareInfo JSON 不得拋錯，回傳 unknown 結果。"""
    result = build_fee_summary(
        "{not-json", "", day(), "weekday")

    assert result == averaged_result()


def test_non_dict_json_returns_unknown():
    """FareInfo 不是物件時視同無結構化費率。"""
    result = build_fee_summary(
        json.dumps(["not", "a", "rule"]), "", day(), "weekday")

    assert result == averaged_result()


def test_extracts_small_car_hourly_fee_and_cap_from_text():
    result = build_fee_summary(
        None, "小型車每小時 40 元，當日最高 240 元；機車每次 20 元",
        datetime.fromisoformat("2026-08-19T18:00:00+08:00"), "weekday")
    assert result["hourly_fee_label"] == "40 元／時"
    assert result["hourly_fee_value"] == 40
    assert result["daily_cap_label"] == "240 元"


def test_does_not_use_motorcycle_or_monthly_numbers_as_daily_cap():
    result = build_fee_summary(
        None, "小型車每小時 30 元，月租 3000 元；機車每日最高 50 元",
        datetime.fromisoformat("2026-08-19T18:00:00+08:00"), "weekday")
    assert result["daily_cap_label"] == "官方未標示"


def test_day_specific_text_prices_follow_arrival_day():
    """文字明確區分平假日時，依抵達日選價格，不把已知條件降級成區間。"""
    weekday = build_fee_summary(
        None, "平日每小時 60 元，假日每小時 80 元",
        datetime.fromisoformat("2026-08-19T18:00:00+08:00"), "weekday")
    holiday = build_fee_summary(
        None, "平日每小時 60 元，假日每小時 80 元",
        datetime.fromisoformat("2026-08-19T18:00:00+08:00"), "holiday")

    assert weekday["hourly_fee_label"] == "60 元／時"
    assert weekday["hourly_fee_value"] == 60
    assert weekday["fee_note"] is None
    assert holiday["hourly_fee_label"] == "80 元／時"
    assert holiday["hourly_fee_value"] == 80


def test_half_hour_price_normalized_to_hourly_display():
    """每半小時 20 元換算為每小時 40 元顯示，並仍取每日上限。"""
    result = build_fee_summary(
        None, "小型車每半小時 20 元，每日最高 160 元",
        datetime.fromisoformat("2026-08-19T18:00:00+08:00"), "weekday")

    assert result["hourly_fee_label"] == "40 元／時"
    assert result["daily_cap_label"] == "160 元"
    assert result["fee_confidence"] == "exact"


def test_surcharge_after_cap_word_is_not_a_daily_cap():
    """「超過上限後加收」中的金額是溢時費率，不得誤當每日上限。"""
    result = build_fee_summary(
        None, "小型車每小時 40 元，超過上限後每小時加收 100 元",
        datetime.fromisoformat("2026-08-19T18:00:00+08:00"), "weekday")

    assert result["daily_cap_label"] == "官方未標示"


def test_monthly_cap_word_before_phrase_is_not_a_daily_cap():
    """「月租上限 3000」中上限詞前的月租字樣代表月費，不得當每日上限。"""
    result = build_fee_summary(
        None, "小型車每小時 30 元，月租上限 3000 元",
        datetime.fromisoformat("2026-08-19T18:00:00+08:00"), "weekday")

    assert result["daily_cap_label"] == "官方未標示"


def test_per_entry_cap_word_before_phrase_is_not_a_daily_cap():
    """「每次停車上限 100」中的金額是計次費用，不得當每日上限。"""
    result = build_fee_summary(
        None, "小型車每小時 30 元，每次停車上限 100 元",
        datetime.fromisoformat("2026-08-19T18:00:00+08:00"), "weekday")

    assert result["daily_cap_label"] == "官方未標示"


def test_explicit_day_text_confirms_structured_weekday_price_without_ambiguity():
    """文字已明確選中抵達日價格時，可和結構化時費交叉確認，不必標成歧異。"""
    result = build_fee_summary(
        rules({"ParkingType": "C", "RateType": "1", "ChargeableSTime": "0800",
               "ChargeableETime": "2200", "ParkingRates": "60"}),
        "平日每小時 60 元，假日每小時 80 元",
        datetime.fromisoformat("2026-08-19T18:00:00+08:00"), "weekday")

    assert result["hourly_fee_label"] == "60 元／時"
    assert result["fee_confidence"] == "exact"
    assert result["fee_note"] is None


def test_monthly_fee_payment_cap_words_are_not_daily_cap():
    """「月繳／月費／雙月」上限是月租性質金額，不得視為每日上限。"""
    for text in ("小型車每小時 40 元，月繳上限 3000 元",
                 "小型車每小時 40 元，月費上限 3000 元",
                 "小型車每小時 40 元，雙月上限 5000 元",
                 "小型車每小時 40 元，雙月繳上限 5000 元"):
        result = build_fee_summary(
            None, text, datetime.fromisoformat("2026-08-19T18:00:00+08:00"), "weekday")
        assert result["daily_cap_label"] == "官方未標示"


def test_rate_type_1_accepts_float_or_leading_zero_writing():
    """RateType 1 以 01／1.0 等寫法出現時仍應視為計時費率。"""
    for rate_type in ("1", "01", "1.0", 1, 1.0):
        result = build_fee_summary(rules(
            {"ParkingType": "C", "RateType": rate_type, "ChargeableSTime": "0800",
             "ChargeableETime": "2200", "ParkingRates": "60"},
        ), "", day(), "weekday")
        assert result["hourly_fee_label"] == "60 元／時"
        assert result["fee_confidence"] == "exact"


def test_heavy_motorcycle_parenthetical_keeps_small_car_weekday_and_weekend_rates():
    """小型車說明內的「大型重型機車」不是機車費率段落，不能把後方汽車費率切掉。"""
    fee_info = (
        "計時：小型車(含大型重型機車)週一至週五30元/時，"
        "週六至週日及政府行政機關放假之紀念日、民俗節日40元/時，"
        "機車10元/時，停車全程以半小時計"
    )

    weekday = build_fee_summary(None, fee_info, day(), "weekday")
    weekend = build_fee_summary(None, fee_info, day(), "weekend")
    holiday = build_fee_summary(None, fee_info, day(), "holiday")

    assert weekday["hourly_fee_label"] == "30 元／時"
    assert weekday["hourly_fee_value"] == 30
    assert weekend["hourly_fee_label"] == "40 元／時"
    assert weekend["hourly_fee_value"] == 40
    assert holiday["hourly_fee_label"] == "40 元／時"
    assert holiday["hourly_fee_value"] == 40


def test_independent_motorcycle_clause_never_changes_small_car_hourly_fee_or_cap():
    """獨立的機車費率與機車上限不能混入小型車摘要。"""
    result = build_fee_summary(
        None,
        "小型車每小時30元；機車每小時10元，機車當日當次上限20元",
        day(), "weekday")

    assert result["hourly_fee_label"] == "30 元／時"
    assert result["hourly_fee_value"] == 30
    assert result["daily_cap_label"] == "官方未標示"


def test_unparsed_official_fee_text_is_not_mislabeled_as_missing():
    """官方有費率說明但無法安全換算時，應引導看原文而非宣稱官方沒寫。"""
    result = build_fee_summary(
        None, "小型車採累進費率，實際金額依現場公告", day(), "weekday")

    assert result["hourly_fee_label"] == "請查看官方費率"
    assert result["hourly_fee_value"] is None
    assert result["fee_confidence"] == "unparsed"
    assert result["fee_note"] == "官方費率格式較複雜"


def test_common_weekday_marker_variants_select_the_matching_price():
    """週／周／星期及省略第二個週字的常見日期格式，都要依抵達日選價。"""
    cases = (
        "星期一至星期五50元/時，星期六至星期日70元/時",
        "周一到周五50元/時，周六到周日70元/時",
        "週一～五50元/時，週六～日70元/時",
    )
    for fee_info in cases:
        weekday = build_fee_summary(None, fee_info, day(), "weekday")
        weekend = build_fee_summary(None, fee_info, day(), "weekend")
        assert weekday["hourly_fee_label"] == "50 元／時"
        assert weekend["hourly_fee_label"] == "70 元／時"
