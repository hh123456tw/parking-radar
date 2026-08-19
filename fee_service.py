"""解析小型車費率：從 FareInfo 結構化規則與官方 payex 文字保守推算時費與每日上限。

本模組僅負責顯示字串，不會影響推薦排序，也不需要任何網路或資料庫連線。
"""

import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")

SMALL_CAR_TYPES = {"C", "CM"}
CAP_PHRASES = ("當日最高", "每日最高", "24 小時最高", "24小時最高", "上限")
# 費用數字若與這些字眼同一段，通常是月租／計次／溢時費率，不得視為每日上限。
SKIP_CAP_TOKENS = ("月租", "月票", "每月", "月繳", "月費", "雙月", "每次", "計次", "每小時", "半小時", "加收", "逾時")

HALF_HOUR_RE = re.compile(r"每半小時\s*(\d+(?:\.\d+)?)\s*元?")
HOUR_RE = re.compile(r"每小時\s*(\d+(?:\.\d+)?)\s*元?")
PER_HOUR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*元?\s*[／/]\s*(?:每?小)?時")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

UNKNOWN_LABEL = "官方未標示"
AMBIGUITY_NOTE = "依日期、活動或現場公告"


def _normalize_rules(fare_rules_json):
    """把 FareInfo JSON 轉成 FareRule 清單；格式無效時回傳空清單。"""
    if not fare_rules_json:
        return []
    try:
        data = json.loads(fare_rules_json)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    rules = data.get("FareRule")
    if rules is None:
        return []
    if isinstance(rules, dict):
        rules = [rules]
    if not isinstance(rules, list):
        return []
    return rules


def _parse_minutes(value):
    """把 0800／2400 四碼時刻轉成分鐘數；格式不符時回傳 None。"""
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) != 4:
        return None
    minutes = int(digits[:2]) * 60 + int(digits[2:])
    if minutes > 24 * 60:
        return None
    return minutes


def _in_range(minute, start, end):
    """判斷分鐘是否落在費率時段；跨越午夜（如 2200–0800）或 2400 終點皆支援。"""
    if start == end:
        return False
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def _price_from_rate(value):
    """從 ParkingRates 取出第一個金額；無法解析或非正值時回傳 None。"""
    match = NUMBER_RE.search(str(value or ""))
    if not match:
        return None
    price = round(float(match.group(0)))
    return price if price > 0 else None


def _is_hourly_rate_type(rate_type):
    """只有 RateType 1（計時）可作為每小時費率；相容 1.0／01 等資料寫法。"""
    try:
        return float(str(rate_type or "")) == 1.0
    except ValueError:
        return False


def _structured_hourly_prices(fare_rules_json, arrival_time):
    """回傳抵達時段內適用的小型車計時費率，去重複後依序排列。"""
    if arrival_time.tzinfo is not None:
        local = arrival_time.astimezone(TAIPEI)
    else:
        local = arrival_time
    minute = local.hour * 60 + local.minute
    prices = []
    for rule in _normalize_rules(fare_rules_json):
        if not isinstance(rule, dict):
            continue
        if rule.get("ParkingType") not in SMALL_CAR_TYPES:
            continue  # 機車／月租規則一律不列入。
        if not _is_hourly_rate_type(rule.get("RateType")):
            continue  # 只有 RateType 1（計時）可作為每小時費率。
        start = _parse_minutes(rule.get("ChargeableSTime"))
        end = _parse_minutes(rule.get("ChargeableETime"))
        if start is None or end is None:
            continue
        if not _in_range(minute, start, end):
            continue
        price = _price_from_rate(rule.get("ParkingRates"))
        if price is None:
            continue
        prices.append(price)
    return list(dict.fromkeys(prices))


def _small_car_segment(fee_info):
    """切出機車標題前的小型車計時費率文字段。"""
    if not fee_info:
        return ""
    text = str(fee_info)
    for marker in ("機車", "重機"):
        pos = text.find(marker)
        if pos != -1:
            return text[:pos]
    return text


def _hourly_prices_from_text(fee_info):
    """從小型車文字段解析每小時金額（每半小時價乘以二後去重複）。"""
    segment = _small_car_segment(fee_info)
    if not segment:
        return []
    prices = []
    for match in HOUR_RE.finditer(segment):
        price = _price_from_rate(match.group(1))
        if price is not None:
            prices.append(price)
    for match in PER_HOUR_RE.finditer(segment):
        price = _price_from_rate(match.group(1))
        if price is not None:
            prices.append(price)
    for match in HALF_HOUR_RE.finditer(segment):
        price = _price_from_rate(match.group(1))
        if price is not None:
            prices.append(price * 2)
    return list(dict.fromkeys(prices))


def _daily_cap_from_text(fee_info):
    """只在小型車段且含認可上限詞時取上限金額；月租、計次、溢時等不得使用。

    上限詞前後各掃一小段檢查跳過字眼：若「月租上限 3000」「每次停車上限 100」
    等月租／計次詞語出現在上限詞之前，該數字屬月租或計次費用而非每日上限。
    """
    segment = _small_car_segment(fee_info)
    if not segment:
        return None
    before_window = 6
    after_window = 12
    for phrase in CAP_PHRASES:
        pos = segment.find(phrase)
        while pos != -1:
            before = segment[max(0, pos - before_window): pos]
            after = segment[pos + len(phrase): pos + len(phrase) + after_window]
            if not any(token in before or token in after for token in SKIP_CAP_TOKENS):
                cap = _price_from_rate(after)
                if cap is not None:
                    return cap
            pos = segment.find(phrase, pos + 1)
    return None


def _describe_hourly(prices):
    """依費率清單組出顯示標籤與信心等級；單一金額精確，多個金額回傳區間。"""
    unique = sorted(set(prices))
    if len(unique) == 1:
        return f"{unique[0]} 元／時", "exact"
    return f"{unique[0]}～{unique[-1]} 元／時", "range"


def build_fee_summary(fare_rules_json: str | None, fee_info: str | None,
                      arrival_time: datetime, day_kind: str) -> dict:
    """組合小型車時費與每日上限的顯示字串；資料不足時絕不臆測金額。

    結構化 FareInfo 規則有效時以規則為準，官方文字僅用於每日上限與歧異備註；
    day_kind 保留給後續推薦排序使用，本任務（純顯示）不納入推算。
    """
    structured = _structured_hourly_prices(fare_rules_json, arrival_time)
    text_prices = _hourly_prices_from_text(fee_info)
    cap = _daily_cap_from_text(fee_info)

    hourly_label = UNKNOWN_LABEL
    confidence = "unknown"
    note = None
    if structured:
        hourly_label, confidence = _describe_hourly(structured)
        if len(set(text_prices)) > 1:
            note = AMBIGUITY_NOTE
    elif text_prices:
        hourly_label, confidence = _describe_hourly(text_prices)
        if confidence == "range":
            note = AMBIGUITY_NOTE

    return {
        "hourly_fee_label": hourly_label,
        "daily_cap_label": f"{cap} 元" if cap is not None else UNKNOWN_LABEL,
        "fee_note": note,
        "fee_confidence": confidence,
    }
