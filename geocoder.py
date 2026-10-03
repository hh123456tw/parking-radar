"""免費地址轉座標：先查 MySQL 快取，再以受限制的 Nominatim 請求補齊。"""

import re
import time
from datetime import datetime, timezone

import requests

from ai_service import TAIPEI_DISTRICTS
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

def resolve_known_landmark(name):
    """只把少量穩定展示地標換成門牌，其餘交給通用候選流程。"""
    key = re.sub(r"\s+", "", name.strip()).replace("台北市", "臺北市")
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
            headers={"User-Agent": Config.NOMINATIM_USER_AGENT},
            timeout=Config.GEOCODER_TIMEOUT_SECONDS,
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


def geocode_candidates(candidates, connection, http_get=requests.get, limit=3):
    """驗證最多三個 Gemini 地址候選，排除查無資料與重複座標。"""
    verified = []
    seen_coordinates = set()
    for candidate in candidates[:limit]:
        address = (candidate.get("address") or "").strip()
        if not address:
            continue
        result = geocode_address(address, connection, http_get=http_get)
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
            "district": candidate.get("district"),
            "display_address": result["display_address"],
            "latitude": float(result["latitude"]),
            "longitude": float(result["longitude"]),
        })
    return verified


# OpenStreetMap 地址由小到大排列；路名以路、街、大道、段、巷、弄結尾，門牌為純數字或「12-1」。
ROAD_RE = re.compile(r"(路|街|大道|段|巷|弄)$")
HOUSE_NUMBER_RE = re.compile(r"^\d+(-\d+)?$")


def format_place(display_address):
    """把「名稱, 門牌, 路, 里, 區, 商圈, 臺北市, 郵遞區號, 臺灣」整理成名稱＋臺灣習慣地址。

    回傳 {"name", "address"}：純路段查詢沒有名稱時 name 為 None；
    認不出行政區與路名時不硬拼地址，address 為 None、name 保留第一段。
    """
    parts = [part.strip() for part in str(display_address or "").split(",") if part.strip()]
    if not parts:
        return {"name": None, "address": None}
    district = next((part for part in parts if part in TAIPEI_DISTRICTS), None)
    road_index = next((index for index, part in enumerate(parts)
                       if ROAD_RE.search(part)), None)
    road = parts[road_index] if road_index is not None else None
    number = next((part for part in parts[:road_index or 0]
                   if HOUSE_NUMBER_RE.match(part)), None)
    first = parts[0]
    name = None if first in {road, number} else first
    if district is None and road is None:
        return {"name": first, "address": None}
    address = "臺北市" + (district or "") + (road or "") + (f"{number}號" if number else "")
    return {"name": name, "address": address}
