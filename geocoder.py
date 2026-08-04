"""免費地址轉座標：先查 MySQL 快取，再以受限制的 Nominatim 請求補齊。"""

import re
import time
from datetime import datetime, timezone
import requests
from config import Config
from database import get_cached_geocode, save_cached_geocode

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_last_request_at = 0.0

# 期中專題只維護少量常見地標，避免發展成龐大的地標資料庫。
LANDMARK_ALIASES = {
    "台北車站": "臺北市中正區北平西路3號",
    "臺北車站": "臺北市中正區北平西路3號",
    "台北市政府": "臺北市信義區市府路1號",
    "臺北市政府": "臺北市信義區市府路1號",
}

# 多據點機構不能由系統猜測，以免把使用者帶到錯誤行政區。
AMBIGUOUS_LANDMARKS = {
    "資策會": "資策會有多個臺北據點，請輸入完整地址或單位名稱",
}


def resolve_known_landmark(name):
    """把少量單一地標換成門牌；多據點地標要求使用者補充。"""
    key = re.sub(r"\s+", "", name.strip()).replace("台北市", "臺北市")
    if key in AMBIGUOUS_LANDMARKS:
        raise ValueError(AMBIGUOUS_LANDMARKS[key])
    return LANDMARK_ALIASES.get(key, name.strip())


def normalize_address(address):
    """統一台／臺、移除空白，並在缺少城市時補上臺北市。"""
    normalized = re.sub(r"\s+", "", address.strip()).replace("台北市", "臺北市")
    # 地標查詢可能是「台北車站,中正區,臺北市」；已有城市就不能再加一次。
    if "臺北市" not in normalized:
        normalized = "臺北市" + normalized
    return normalized


def nominatim_queries(address):
    """建立查詢候選；完整臺北門牌優先改成門牌、道路、行政區順序。"""
    normalized = normalize_address(address)
    match = re.match(
        r"^臺北市(?P<district>.+?區)(?:(?P<village>.+?里))?"
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
    parts.extend([match.group("district"), "臺北市"])
    return [", ".join(parts), normalized]


def _respect_rate_limit():
    """確保同一程序兩次公共 Nominatim 請求至少間隔一秒。"""
    global _last_request_at
    wait_seconds = 1.0 - (time.monotonic() - _last_request_at)
    if wait_seconds > 0:
        time.sleep(wait_seconds)
    _last_request_at = time.monotonic()


def geocode_address(address, connection, http_get=requests.get):
    """回傳快取或第一筆臺北市座標；查無結果時回傳 None。"""
    key = normalize_address(address)
    cached = get_cached_geocode(connection, key)
    if cached:
        return cached

    for query in nominatim_queries(address):
        _respect_rate_limit()
        response = http_get(
            NOMINATIM_URL,
            params={"q": query, "format": "jsonv2", "limit": 1,
                    "countrycodes": "tw"},
            headers={"User-Agent": Config.NOMINATIM_USER_AGENT}, timeout=8,
        )
        response.raise_for_status()
        items = response.json()
        if not items or "臺北市" not in items[0].get("display_name", ""):
            continue
        result = {
            "normalized_address": key,
            "display_address": items[0]["display_name"],
            "latitude": float(items[0]["lat"]),
            "longitude": float(items[0]["lon"]),
            "cached_at": datetime.now(timezone.utc),
        }
        save_cached_geocode(connection, result)
        connection.commit()
        return result
    return None
