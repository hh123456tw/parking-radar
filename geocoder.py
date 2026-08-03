"""免費地址轉座標：先查 MySQL 快取，再以受限制的 Nominatim 請求補齊。"""

import re
import time
from datetime import datetime, timezone
import requests
from config import Config
from database import get_cached_geocode, save_cached_geocode

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_last_request_at = 0.0


def normalize_address(address):
    """統一台／臺、移除空白，並在缺少城市時補上臺北市。"""
    normalized = re.sub(r"\s+", "", address.strip()).replace("台北市", "臺北市")
    if not normalized.startswith("臺北市"):
        normalized = "臺北市" + normalized
    return normalized


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
    _respect_rate_limit()
    response = http_get(
        NOMINATIM_URL,
        params={"q": key, "format": "jsonv2", "limit": 1, "countrycodes": "tw"},
        headers={"User-Agent": Config.NOMINATIM_USER_AGENT}, timeout=8,
    )
    response.raise_for_status()
    items = response.json()
    if not items or "臺北市" not in items[0].get("display_name", ""):
        return None
    result = {
        "normalized_address": key, "display_address": items[0]["display_name"],
        "latitude": float(items[0]["lat"]), "longitude": float(items[0]["lon"]),
        "cached_at": datetime.now(timezone.utc),
    }
    save_cached_geocode(connection, result)
    connection.commit()
    return result
