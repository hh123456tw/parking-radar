"""純分析函式：負責清洗、地獄指數、距離、推薦分組與歷史折線資料。"""

from datetime import timezone
from math import asin, cos, radians, sin, sqrt
from zoneinfo import ZoneInfo

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


def _legacy_score(current_hell, distance_m=None, radius_m=1500):
    """舊推薦分數（不輸出），只用來讓同分場站維持原本順序；公式與兩次 round() 照舊。

    沒有歷史樣本時舊版用 60/40；行政區模式沒有距離時就是空位容易度本身。
    """
    current_ease = 100 - current_hell
    if distance_m is None:
        return round(current_ease, 2)
    nearby_ease = round(max(0.0, min(100.0, 100 * (1 - distance_m / radius_m))), 2)
    return round(current_ease * 0.6 + nearby_ease * 0.4, 2)


def rank_candidates(rows, destination_lat, destination_lon, radius_m=1500):
    """排除無效或超出半徑的場站並加入分析欄位，依舊推薦分數穩定排序以保留同分順序。"""
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
        candidates.append((_legacy_score(item["hell_score"], distance, radius_m), item))
    # 後續 split_recommendation_groups 與 select_walking_candidates 都是穩定排序，
    # 同分時沿用這裡的順序，結果才與移除推薦分數前完全相同。
    return [item for _, item in sorted(candidates, key=lambda pair: pair[0], reverse=True)]


def _pressure_label(score):
    """把停車壓力分數翻譯成低、中、高、極高。"""
    if score >= 95:
        return "極高"
    if score >= 85:
        return "高"
    if score >= 60:
        return "中"
    return "低"


def explain_candidate(row):
    """用固定規則產生決策狀態與最多三條白話原因。"""
    item = dict(row)
    available = int(item["available_spaces"])
    total = int(item["total_spaces"])
    pressure = float(item["hell_score"])
    free_ratio = available / total

    if available <= 3:
        status, label = "avoid", "不建議前往"
    elif available <= 10 or free_ratio < 0.1:
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

    walking_distance = item.get("walking_distance_m")
    walking_minutes = item.get("walking_duration_minutes")
    distance = item.get("distance_m")
    if walking_distance is not None and walking_minutes is not None:
        distance_reason = (
            f"步行約 {max(1, round(walking_minutes))} 分鐘"
            f"（{round(walking_distance)} 公尺）"
        )
    elif distance is None:
        distance_reason = "目前以行政區整體狀況比較"
    elif distance <= 500:
        distance_reason = f"距目的地近，約 {round(distance)} 公尺"
    elif distance <= 1000:
        distance_reason = f"距目的地約 {round(distance)} 公尺"
    else:
        distance_reason = f"距目的地較遠，約 {distance / 1000:.1f} 公里"

    reasons = [availability_reason, distance_reason]
    if status == "avoid":
        reasons.append("建議改看推薦前往清單")
    elif status == "warning":
        reasons.append("建議保留下一個選擇")

    item.update(
        decision_status=status,
        decision_label=label,
        pressure_label=_pressure_label(pressure),
        reasons=reasons,
    )
    return item


def split_recommendation_groups(ranked):
    """優先輸出所有安全場站；安全場站不足三座時才以備選補足。"""
    explained = [explain_candidate(row) for row in ranked]

    def proximity_key(item):
        """實際步行資料優先；未知路線者才在後段依直線距離補位。"""
        walking = item.get("walking_duration_minutes")
        if walking is not None:
            return (0, float(walking))
        distance = item.get("distance_m")
        return (1, float("inf") if distance is None else float(distance))

    def distance_then_spaces(item):
        """同風險優先選步行較近場站；相同時再選剩餘格數較多者，再相同則保留 rank_* 的順序。"""
        return (*proximity_key(item), -int(item["available_spaces"]))

    safe = sorted(
        (row for row in explained if row["decision_status"] == "recommended"),
        key=distance_then_spaces,
    )
    backup = sorted(
        (row for row in explained if row["decision_status"] == "warning"),
        key=distance_then_spaces,
    )
    excluded_count = sum(
        row["decision_status"] == "avoid" for row in explained)
    recommendations = (safe + backup)[:3]
    selected_ids = {row["lot_id"] for row in recommendations}
    warning = (
        [row for row in backup if row["lot_id"] not in selected_ids][:3]
        if len(safe) < 3 else []
    )
    return {
        "recommendations": recommendations,
        "other_recommended": safe[3:],
        "warning": warning,
        "recommended_count": len(safe),
        "excluded_count": excluded_count,
    }


def select_walking_candidates(ranked, limit=15):
    """有限路線名額只給可前往與備選場站，優先計算安全場站。"""
    status_order = {"recommended": 0, "warning": 1}
    explained = [
        row for row in (explain_candidate(item) for item in ranked)
        if row["decision_status"] in status_order
    ]
    return sorted(
        explained,
        key=lambda row: (
            status_order[row["decision_status"]],
            float("inf") if row.get("distance_m") is None
            else float(row["distance_m"]),
        ),
    )[:limit]


def rank_district_candidates(rows):
    """手動行政區模式不計距離，只保留有效場站，依舊推薦分數穩定排序。"""
    ranked = []
    for row in rows:
        current = hell_score(row["total_spaces"], row["available_spaces"])
        if current is None:
            continue
        ranked.append(dict(row, distance_m=None, hell_score=current,
                           hell_label=hell_label(current)))
    return sorted(ranked, key=lambda item: _legacy_score(item["hell_score"]), reverse=True)


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
