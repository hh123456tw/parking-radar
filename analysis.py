"""純分析函式：負責清洗、地獄指數、距離、推薦與歷史統計。"""

from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt
from zoneinfo import ZoneInfo

import pandas as pd

TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def clean_available(total_spaces, available_spaces):
    """驗證總格數與剩餘格數；有效時回傳 int，否則回傳 None。"""
    if total_spaces is None or available_spaces is None:
        return None
    total = int(total_spaces)
    available = int(available_spaces)
    if total <= 0 or available < 0 or available > total:
        return None
    return available


def hell_score(total_spaces, available_spaces):
    """依有效格數計算 0 到 100 的使用率分數，無效資料回傳 None。"""
    available = clean_available(total_spaces, available_spaces)
    if available is None:
        return None
    return round((int(total_spaces) - available) / int(total_spaces) * 100, 2)


def hell_label(score):
    """把地獄指數轉成人類可讀的四級標籤。"""
    if score >= 95:
        return "停車地獄"
    if score >= 80:
        return "很難停"
    if score >= 60:
        return "開始擠"
    return "輕鬆停"


def district_hell_score(rows):
    """以有效總車位加權計算行政區地獄指數，而非平均場站分數。"""
    valid = [
        row for row in rows
        if clean_available(row.get("total_spaces"), row.get("available_spaces"))
        is not None
    ]
    total = sum(int(row["total_spaces"]) for row in valid)
    if total == 0:
        return None
    available = sum(int(row["available_spaces"]) for row in valid)
    return round((total - available) / total * 100, 2)


def haversine_m(lat1, lon1, lat2, lon2):
    """用 Haversine 公式回傳兩組 WGS84 座標的直線公尺數。"""
    lat1, lon1, lat2, lon2 = map(float, (lat1, lon1, lat2, lon2))
    earth_radius_m = 6_371_000
    lat1_r, lat2_r = radians(lat1), radians(lat2)
    delta_lat = radians(lat2 - lat1)
    delta_lon = radians(lon2 - lon1)
    value = sin(delta_lat / 2) ** 2 + cos(lat1_r) * cos(lat2_r) * sin(delta_lon / 2) ** 2
    return earth_radius_m * 2 * asin(sqrt(value))


def distance_ease(distance_m, radius_m=1500):
    """把 0 到搜尋半徑的距離線性轉成 100 到 0 的容易度。"""
    return round(max(0.0, min(100.0, 100 * (1 - distance_m / radius_m))), 2)


def recommendation_score(current_hell, distance_m, historical_hell=None, radius_m=1500):
    """依歷史樣本是否存在，套用鎖定的 50/30/20 或 60/40 權重。"""
    current_ease = 100 - current_hell
    nearby_ease = distance_ease(distance_m, radius_m)
    if historical_hell is None:
        score = current_ease * 0.6 + nearby_ease * 0.4
    else:
        score = current_ease * 0.5 + nearby_ease * 0.3 + (100 - historical_hell) * 0.2
    return round(score, 2)


def rank_candidates(rows, destination_lat, destination_lon, radius_m=1500):
    """排除無效或超出半徑的場站，加入分析欄位並依推薦分數排序。"""
    candidates = []
    for row in rows:
        if clean_available(row.get("total_spaces"), row.get("available_spaces")) is None:
            continue
        if row.get("latitude") is None or row.get("longitude") is None:
            continue
        distance = haversine_m(destination_lat, destination_lon, row["latitude"], row["longitude"])
        if distance > radius_m:
            continue
        item = dict(row)
        item["distance_m"] = round(distance, 1)
        item["hell_score"] = hell_score(row["total_spaces"], row["available_spaces"])
        item["hell_label"] = hell_label(item["hell_score"])
        item["recommendation_score"] = recommendation_score(
            item["hell_score"], distance, row.get("historical_hell_score"), radius_m
        )
        candidates.append(item)
    return sorted(candidates, key=lambda item: item["recommendation_score"], reverse=True)


def _pressure_label(score):
    """把停車壓力分數翻譯成低、中、高、極高。"""
    if score >= 95:
        return "極高"
    if score >= 85:
        return "高"
    if score >= 60:
        return "中"
    return "低"


def _recommendation_label(score):
    """把綜合推薦分數翻譯成高、中、低。"""
    if score >= 80:
        return "高"
    if score >= 60:
        return "中"
    return "低"


def explain_candidate(row, min_history_samples=3):
    """用固定規則產生決策狀態與最多三條白話原因。"""
    item = dict(row)
    available = int(item["available_spaces"])
    total = int(item["total_spaces"])
    pressure = float(item["hell_score"])
    recommendation = float(item["recommendation_score"])
    free_ratio = available / total

    if available <= 3:
        status, label = "avoid", "不建議前往"
    elif ((available <= 15 and free_ratio < 0.5)
          or (available <= 30 and free_ratio < 0.1)):
        status, label = "warning", "建議備選"
    else:
        status, label = "recommended", "可以前往"

    if available == 0:
        availability_reason = "目前已滿場"
    elif available <= 3:
        availability_reason = f"目前只剩 {available} 格，抵達前可能滿場"
    elif status == "warning":
        availability_reason = f"目前 {available} / {total} 格可停，抵達前請再確認"
    else:
        availability_reason = f"目前仍有 {available} 格（共 {total} 格），空位數充足"

    distance = item.get("distance_m")
    if distance is None:
        distance_reason = "目前以行政區整體狀況比較"
    elif distance <= 500:
        distance_reason = f"距目的地近，約 {round(distance)} 公尺"
    elif distance <= 1000:
        distance_reason = f"距目的地約 {round(distance)} 公尺"
    else:
        distance_reason = f"距目的地較遠，約 {distance / 1000:.1f} 公里"

    if status == "avoid":
        final_reason = "建議改看推薦前往清單"
    elif status == "warning":
        final_reason = "建議保留下一個選擇"
    elif (item.get("history_sample_count") or 0) < min_history_samples:
        final_reason = "歷史樣本不足，未納入判斷"
    else:
        historical = item.get("historical_hell_score")
        final_reason = (
            f"相同時段歷史停車壓力約 {round(historical)} 分"
            if historical is not None else "歷史樣本不足，未納入判斷"
        )

    item.update(
        decision_status=status,
        decision_label=label,
        pressure_label=_pressure_label(pressure),
        recommendation_label=_recommendation_label(recommendation),
        reasons=[availability_reason, distance_reason, final_reason],
    )
    return item


def split_recommendation_groups(ranked):
    """加入決策說明並產生互斥的推薦、警示與避雷群組。"""
    explained = [explain_candidate(row) for row in ranked]
    with_distance = [row for row in explained if row.get("distance_m") is not None]
    nearest = sorted(with_distance, key=lambda item: item["distance_m"])[:3]
    recommendations = [
        row for row in explained if row["decision_status"] == "recommended"][:3]
    warning = [row for row in explained if row["decision_status"] == "warning"][:3]
    avoid = [row for row in explained if row["decision_status"] == "avoid"][:3]
    return {
        "recommendations": recommendations,
        "nearest": nearest,
        "warning": warning,
        "avoid": avoid,
    }


def rank_district_candidates(rows):
    """手動行政區模式不計距離，只依即時 80% 與歷史 20% 排序。"""
    ranked = []
    for row in rows:
        current = hell_score(row["total_spaces"], row["available_spaces"])
        if current is None:
            continue
        historical = row.get("historical_hell_score")
        score = (100 - current) if historical is None else (100 - current) * 0.8 + (100 - historical) * 0.2
        item = dict(row, distance_m=None, hell_score=current,
                    hell_label=hell_label(current), recommendation_score=round(score, 2))
        ranked.append(item)
    return sorted(ranked, key=lambda item: item["recommendation_score"], reverse=True)


def day_type(local_dt):
    """星期一至五回傳 weekday，星期六日回傳 weekend。"""
    return "weekday" if local_dt.weekday() < 5 else "weekend"


def summarize_matching_history(rows, arrival_time, min_samples=3):
    """計算與抵達時間同日別、同整點小時的平均地獄指數。"""
    local_arrival = arrival_time.astimezone(TAIPEI_TZ)
    target_type = day_type(local_arrival)
    target_hour = local_arrival.hour
    frame = pd.DataFrame(rows)
    if frame.empty:
        return {"hell_score": None, "sample_count": 0,
                "day_type": target_type, "hour": target_hour}
    frame["local_time"] = pd.to_datetime(frame["captured_at"], utc=True).dt.tz_convert("Asia/Taipei")
    frame["day_type"] = frame["local_time"].dt.weekday.map(
        lambda value: "weekday" if value < 5 else "weekend")
    frame["hour"] = frame["local_time"].dt.hour
    frame["hell_score"] = frame.apply(
        lambda row: hell_score(row["total_spaces"], row["available_spaces"]), axis=1)
    matched = frame[(frame["day_type"] == target_type) &
                    (frame["hour"] == target_hour)]["hell_score"].dropna()
    result = {"hell_score": None, "sample_count": int(len(matched)),
              "day_type": target_type, "hour": target_hour}
    if len(matched) >= min_samples:
        result["hell_score"] = round(float(matched.mean()), 2)
    return result


def summarize_hour_comparison(rows, hour, min_samples=3):
    """比較指定整點的平日與週末平均分數；各組都獨立套用三筆門檻。"""
    results = {kind: {"hell_score": None, "sample_count": 0}
               for kind in ("weekday", "weekend")}
    for kind, target in (
        ("weekday", datetime(2024, 1, 1, hour, tzinfo=TAIPEI_TZ)),
        ("weekend", datetime(2024, 1, 6, hour, tzinfo=TAIPEI_TZ)),
    ):
        summary = summarize_matching_history(rows, target, min_samples)
        results[kind] = {"hell_score": summary["hell_score"],
                         "sample_count": summary["sample_count"]}
    return results


def build_history_series(rows):
    """將最近七天快照轉成 Chart.js 可直接使用的臺北時間序列。"""
    points = []
    for row in rows:
        if clean_available(row["total_spaces"], row["available_spaces"]) is None:
            continue
        captured = row["captured_at"]
        # PyMySQL 回傳無時區的 UTC 時間，先補上 UTC 再轉臺北時間，避免受主機時區影響。
        if captured.tzinfo is None:
            captured = captured.replace(tzinfo=timezone.utc)
        points.append({
            "captured_at": captured.astimezone(TAIPEI_TZ).isoformat(),
            "available_spaces": int(row["available_spaces"]),
        })
    return points
