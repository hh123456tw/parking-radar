"""純分析函式：負責清洗、地獄指數、距離、推薦與歷史統計。"""

from math import asin, cos, radians, sin, sqrt


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
