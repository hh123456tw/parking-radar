"""場站型態整合：人工覆寫優先，次為官方關鍵字，再以 OSM 補足 unknown。"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

from analysis import haversine_m
from database import (
    fetch_parking_metadata_candidates,
    get_connection,
    update_parking_metadata,
)

OVERRIDES_PATH = Path("data/parking_overrides.json")
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_USER_AGENT = "parking-hell-radar-metadata/1.0"
# 臺北市行政區邊界放寬一點，涵蓋官方入口座標的允許範圍。
TAIPEI_BBOX = (24.8, 121.3, 25.3, 121.8)
ALLOWED_FACILITY_TYPES = frozenset({
    "mechanical", "surface", "underground", "multi_storey", "mixed", "unknown",
})
# OSM 只提供可靠的平面、地下與立體判斷，機械式一律不從 OSM 推論。
OSM_PARKING_TO_FACILITY = {
    "surface": "surface",
    "underground": "underground",
    "multi-storey": "multi_storey",
}
_OFFICIAL_WORD_TYPES = (
    ("機械", "mechanical"),
    ("平面", "surface"),
    ("地下", "underground"),
    ("立體", "multi_storey"),
)


def infer_official_facility_type(name: str, summary: str) -> tuple[str, str]:
    """只依官方文字中的明確關鍵字判斷型態；兩種以上同時出現回傳 mixed。"""
    text = f"{name or ''} {summary or ''}"
    found = {facility for word, facility in _OFFICIAL_WORD_TYPES if word in text}
    if not found:
        return "unknown", "unknown"
    if len(found) > 1:
        return "mixed", "official"
    return found.pop(), "official"


def match_osm_facilities(lots: list[dict], elements: list[dict],
                        max_distance_m: float = 40) -> dict[str, str]:
    """為每個有座標的場站找唯一支援的 OSM 設施；候選不唯一時不加。"""
    matches = {}
    for lot in lots:
        latitude, longitude = lot.get("latitude"), lot.get("longitude")
        if latitude is None or longitude is None:
            continue
        nearby = []
        for element in elements:
            facility = OSM_PARKING_TO_FACILITY.get(element.get("parking"))
            if facility is None:
                continue
            try:
                distance = haversine_m(
                    latitude, longitude, element["lat"], element["lon"])
            except (TypeError, ValueError):
                continue
            if distance <= max_distance_m:
                nearby.append(facility)
        if len(nearby) == 1:
            matches[lot["lot_id"]] = nearby[0]
    return matches


def _flatten_osm_elements(elements):
    """把節點座標與 way/relation 的 center 座標轉成同一種格式。"""
    flat = []
    for element in elements:
        parking = (element.get("tags") or {}).get("parking")
        if "lat" in element and "lon" in element:
            flat.append({"lat": element["lat"], "lon": element["lon"],
                         "parking": parking})
        else:
            center = element.get("center") or {}
            if "lat" in center and "lon" in center:
                flat.append({"lat": center["lat"], "lon": center["lon"],
                             "parking": parking})
    return flat


def fetch_osm_parking_elements(timeout=15):
    """從 Overpass 抓取臺北 bbox 內的 amenity=parking，並攤平座標。"""
    bbox = f"({','.join(str(value) for value in TAIPEI_BBOX)})"
    query = (
        f'[out:json][timeout:{timeout}];'
        f'(node["amenity"="parking"]{bbox};'
        f'way["amenity"="parking"]{bbox};'
        f'relation["amenity"="parking"]{bbox};);'
        'out center;'
    )
    response = requests.get(
        OVERPASS_URL, params={"data": query}, timeout=timeout,
        headers={"User-Agent": OVERPASS_USER_AGENT})
    response.raise_for_status()
    return _flatten_osm_elements(response.json().get("elements", []))


def load_overrides(overrides_path):
    """讀取人工覆寫；鍵必須是字串 lot_id，值必須是允許的型態。"""
    path = Path(overrides_path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"無法讀取覆寫檔案 {path}") from error
    if not isinstance(data, dict):
        raise ValueError(f"覆寫檔案 {path} 必須是 JSON 物件")
    cleaned = {}
    for key, value in data.items():
        if not isinstance(key, str):
            raise ValueError(f"覆寫鍵 {key!r} 必須是字串 lot_id")
        if value not in ALLOWED_FACILITY_TYPES:
            raise ValueError(f"覆寫值 {value!r} 不是允許的型態")
        cleaned[key] = value
    return cleaned


def sync_parking_metadata(connection, overrides_path: Path = OVERRIDES_PATH,
                         timeout: int = 15) -> dict:
    """以 manual > official > osm > unknown 的優先序整理型態並單次交易寫回。"""
    candidates = fetch_parking_metadata_candidates(connection)
    overrides = load_overrides(overrides_path)
    elements = fetch_osm_parking_elements(timeout=timeout)
    matches = match_osm_facilities(candidates, elements)
    checked_at = datetime.now(timezone.utc)
    counts = {"manual": 0, "official": 0, "osm": 0, "unknown": 0}
    updates = []
    for lot in candidates:
        lot_id = lot["lot_id"]
        current_type = lot.get("facility_type")
        current_source = lot.get("facility_source")
        if lot_id in overrides:
            facility_type, facility_source = overrides[lot_id], "manual"
        elif current_source == "official":
            facility_type, facility_source = current_type or "unknown", "official"
        elif current_source == "osm" and current_type:
            if lot_id in matches:
                facility_type, facility_source = matches[lot_id], "osm"
            else:
                facility_type, facility_source = current_type, "osm"
        elif lot_id in matches:
            facility_type, facility_source = matches[lot_id], "osm"
        else:
            facility_type, facility_source = "unknown", "unknown"
        counts[facility_source] += 1
        updates.append({
            "lot_id": lot_id,
            "facility_type": facility_type,
            "facility_source": facility_source,
            "metadata_checked_at": checked_at,
        })
    try:
        update_parking_metadata(connection, updates)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return counts


def main(argv=None):
    """命令列工具：--sync 執行一次完整型態同步並印出各來源統計。"""
    parser = argparse.ArgumentParser(
        description="同步場站型態：人工覆寫、官方關鍵字與 OSM 一次整理")
    parser.add_argument("--sync", action="store_true",
                        help="執行一次完整型態同步")
    args = parser.parse_args(argv)
    if args.sync:
        connection = get_connection()
        try:
            counts = sync_parking_metadata(connection)
        finally:
            connection.close()
        print(counts)


if __name__ == "__main__":
    main()