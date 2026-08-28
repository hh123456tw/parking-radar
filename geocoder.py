"""免費地址轉座標：先查 MySQL 快取，再以受限制的 Nominatim 請求補齊。"""

import re
import time
from datetime import datetime, timezone
import requests
from config import Config
from database import get_cached_geocode, save_cached_geocode
from city_config import CITIES, city_name

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
ORS_GEOCODE_URL = "https://api.openrouteservice.org/geocode/search"
_last_request_at = 0.0

# 期中專題只維護少量常見地標，避免發展成龐大的地標資料庫。
LANDMARK_ALIASES = {
    "台北車站": "臺北市中正區北平西路3號",
    "臺北車站": "臺北市中正區北平西路3號",
    "台北市政府": "臺北市信義區市府路1號",
    "臺北市政府": "臺北市信義區市府路1號",
}

def resolve_known_landmark(name):
    """只把少量穩定展示地標換成門牌，其餘交給通用候選流程。"""
    key = re.sub(r"\s+", "", name.strip()).replace("台北市", "臺北市")
    return LANDMARK_ALIASES.get(key, name.strip())


def _compact_place_name(value):
    """建立地標名稱比對鍵，不把相似但不同的分店誤判成同一地點。"""
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", str(value or "").lower()) \
        .replace("台", "臺").removeprefix("臺北市").removeprefix("新北市")


def _is_direct_place_match(query, candidate):
    """只接受同名地標或少量通用地點後綴，避免擅選特定分館。"""
    query_key = _compact_place_name(query)
    candidate_key = _compact_place_name(candidate)
    return bool(query_key) and (
        candidate_key == query_key
        or candidate_key in {query_key + suffix for suffix in ("商圈", "車站", "捷運站")}
    )


def geocode_fast_landmark(query, api_key, http_get=requests.get):
    """以既有 ORS 金鑰快速解析單純地標；不確定時回傳 None 給 Gemini。"""
    if not api_key or not str(query or "").strip():
        return None
    try:
        response = http_get(
            ORS_GEOCODE_URL,
            params={"api_key": api_key, "text": query,
                    "boundary.country": "TW", "size": 5},
            headers={"Accept": "application/json"},
            timeout=Config.GEOCODER_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        features = payload.get("features", [])
        if not isinstance(features, list):
            return None
        compact_query = re.sub(r"\s+", "", str(query)).replace("台北市", "臺北市")
        expected_city = (
            "taipei" if compact_query.startswith("臺北市")
            else "new_taipei" if compact_query.startswith("新北市")
            else None
        )
        matches = []
        seen_coordinates = set()
        for feature in features:
            try:
                longitude, latitude = map(
                    float, feature.get("geometry", {}).get("coordinates", []))
                properties = feature.get("properties", {})
                city = {"TC": "taipei", "TP": "new_taipei"}.get(
                    properties.get("region_a"))
            except (AttributeError, TypeError, ValueError):
                continue
            if expected_city and city != expected_city:
                continue
            if city and _is_direct_place_match(query, properties.get("name")):
                coordinate_key = (round(latitude, 5), round(longitude, 5))
                if coordinate_key in seen_coordinates:
                    continue
                seen_coordinates.add(coordinate_key)
                matches.append({
                    "display_address": properties.get("label")
                    or properties.get("name") or str(query),
                    "latitude": latitude,
                    "longitude": longitude,
                    "city": city,
                    "district": None,
                })
        return matches[0] if len(matches) == 1 else None
    except (requests.RequestException, AttributeError, TypeError, ValueError,
            KeyError):
        return None


def normalize_address(address, city=None):
    """統一台／臺、移除空白，並在缺少城市時補上指定或預設城市。"""
    normalized = re.sub(r"\s+", "", address.strip()).replace("台北市", "臺北市")
    # 地標查詢可能是「板橋車站,板橋區,新北市」；已有城市就不能再加一次。
    if "臺北市" not in normalized and "新北市" not in normalized:
        prefix = city_name(city) if city else "臺北市"
        normalized = prefix + normalized
    return normalized


def nominatim_queries(address, city=None):
    """建立查詢候選；完整雙北門牌優先改成門牌、道路、行政區順序。"""
    if city is None:
        for code in CITIES:
            if city_name(code) in address:
                city = code
                break
    normalized = normalize_address(address, city=city)
    match = re.match(
        r"^(?:臺北市|新北市)(?P<district>.+?區)(?:(?P<village>.+?里))?"
        r"(?P<street>.+?)(?P<number>\d+(?:-\d+)?號)$",
        normalized,
    )
    if not match:
        return [normalized]

    parts = [
        match.group("number").removesuffix("號"),
        match.group("street"),
    ]
    if match.group("village"):
        parts.append(match.group("village"))
    parts.extend([match.group("district"), city_name(city) if city else "臺北市"])
    return [", ".join(parts), normalized]


def _respect_rate_limit():
    """確保同一程序兩次公共 Nominatim 請求至少間隔一秒。"""
    global _last_request_at
    wait_seconds = 1.0 - (time.monotonic() - _last_request_at)
    if wait_seconds > 0:
        time.sleep(wait_seconds)
    _last_request_at = time.monotonic()


def _verified_city_and_district(display_name):
    """從已驗證 display text 推論城市代碼與行政區；找不到時為 None。"""
    city = None
    for code in CITIES:
        if city_name(code) in display_name:
            city = code
            break
    district = next(
        (d for d in CITIES[city].districts if d in display_name), None) \
        if city else None
    return city, district


def _effective_city(address, city):
    """地址文字已明確命名支援城市時以地址城市為準，否則以要求的城市為準。"""
    normalized = re.sub(r"\s+", "", address or "").replace("台北市", "臺北市")
    for code in CITIES:
        if city_name(code) in normalized:
            return code
    return city


def _city_and_district_from_text(text):
    """從 display text 或正規化地址推論城市代碼與行政區。"""
    city, district = _verified_city_and_district(text)
    if city is not None:
        return city, district
    for code in CITIES:
        if city_name(code) in text:
            city = code
            break
    if city is None:
        return None, None
    district = next(
        (d for d in CITIES[city].districts if d in text), None)
    return city, district


def geocode_address(address, connection, city=None, http_get=requests.get):
    """回傳快取或第一筆雙北座標；查無結果或城市不符時回傳 None。"""
    key = normalize_address(address, city=city)
    # 冷路徑與熱路徑共用同一條有效城市規則，城市衝突才不會因快取溫度
    # 而出現不同結果：地址已含城市時由地址城市主導，否則由要求的城市約束。
    effective_city = _effective_city(address, city)
    cached = get_cached_geocode(connection, key)
    if cached:
        inferred_city, inferred_district = _city_and_district_from_text(
            cached.get("display_address") or cached.get("normalized_address")
            or "")
        if (inferred_city is not None
                and (effective_city is None
                     or inferred_city == effective_city)):
            result = dict(cached)
            result.setdefault("city", inferred_city)
            result.setdefault("district", inferred_district)
            return result
        # 快取城市與有效城市不符時視為 miss，重新走 Nominatim 驗證；
        # 不得讓舊快取靜默覆寫地址或請求決定的城市。

    for query in nominatim_queries(address, city=effective_city):
        _respect_rate_limit()
        response = http_get(
            NOMINATIM_URL,
            params={"q": query, "format": "jsonv2", "limit": 1,
                    "countrycodes": "tw"},
            headers={"User-Agent": Config.NOMINATIM_USER_AGENT},
            timeout=Config.GEOCODER_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        items = response.json()
        if not items:
            continue
        display_name = items[0].get("display_name", "")
        expected_city = city_name(effective_city) if effective_city else None
        if expected_city and expected_city not in display_name:
            continue
        verified_city, district = _verified_city_and_district(display_name)
        if not verified_city:
            continue
        latitude = float(items[0]["lat"])
        longitude = float(items[0]["lon"])
        min_lat, max_lat, min_lon, max_lon = CITIES[verified_city].bounds
        if not (min_lat <= latitude <= max_lat
                and min_lon <= longitude <= max_lon):
            continue
        result = {
            "normalized_address": key,
            "display_address": display_name,
            "latitude": latitude,
            "longitude": longitude,
            "city": verified_city,
            "district": district,
            "cached_at": datetime.now(timezone.utc),
        }
        save_cached_geocode(connection, result)
        connection.commit()
        return result
    return None


def geocode_candidates(candidates, connection, http_get=requests.get, limit=3):
    """驗證最多三個 Gemini 地址候選，排除查無資料與重複座標。"""
    verified = []
    seen_coordinates = set()
    for candidate in candidates[:limit]:
        address = (candidate.get("address") or "").strip()
        if not address:
            continue
        result = geocode_address(
            address, connection, city=candidate.get("city"), http_get=http_get)
        if result is None:
            continue
        coordinate_key = (
            round(float(result["latitude"]), 5),
            round(float(result["longitude"]), 5),
        )
        if coordinate_key in seen_coordinates:
            continue
        seen_coordinates.add(coordinate_key)
        verified.append({
            "name": (candidate.get("name") or result["display_address"]).strip(),
            "address": address,
            "city": result.get("city"),
            # 只使用已驗證 display text 推論的行政區；驗證文字沒有
            # 可辨識行政區時保持 None，不回退 Gemini 猜測值。
            "district": result.get("district"),
            "display_address": result["display_address"],
            "latitude": float(result["latitude"]),
            "longitude": float(result["longitude"]),
        })
    return verified
