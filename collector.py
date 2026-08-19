"""下載臺北市官方停車資料，清洗後以單一交易保存。"""

import argparse
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import requests
from analysis import clean_available
from database import get_connection, insert_snapshots, upsert_parking_lots

STATIC_URL = "https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_alldesc.json"
DYNAMIC_URL = "https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_allavailable.json"
TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def parse_source_time(value):
    """將官方 ISO 或英文 CST 時間轉成帶 UTC 時區的 datetime。"""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        # 臺北 API 目前也可能回傳 Tue Aug 04 12:04:00 CST 2026。
        parsed = datetime.strptime(value, "%a %b %d %H:%M:%S CST %Y")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TAIPEI_TZ)
    return parsed.astimezone(timezone.utc)


def _entrance_coordinates(raw):
    """讀取第一個入口 WGS84 座標；格式不完整時回傳兩個 None。"""
    items = raw.get("EntranceCoord", {}).get("EntrancecoordInfo", [])
    if not items:
        return None, None
    try:
        latitude = float(items[0]["Xcod"])
        longitude = float(items[0]["Ycod"])
    except (KeyError, TypeError, ValueError):
        return None, None
    if not (24.8 <= latitude <= 25.3 and 121.3 <= longitude <= 121.8):
        return None, None
    return latitude, longitude


def parse_static(payload, realtime_ids):
    """把官方靜態欄位統一成 parking_lots 所需格式。"""
    updated_at = parse_source_time(payload["data"]["UPDATETIME"])
    lots = []
    for raw in payload["data"]["park"]:
        latitude, longitude = _entrance_coordinates(raw)
        lots.append({
            "lot_id": str(raw["id"]), "lot_name": raw.get("name", "未命名停車場"),
            "district": raw.get("area", "未知"), "address": raw.get("address", ""),
            "operator_type": raw.get("type2", "未標示"),
            "total_spaces": int(raw.get("totalcar") or 0),
            "fee_info": raw.get("payex", ""), "service_time": raw.get("serviceTime", ""),
            "latitude": latitude, "longitude": longitude,
            "supports_realtime": str(raw["id"]) in realtime_ids,
            "source_updated_at": updated_at,
            # 官方費率規則保留原始 JSON，讓後續任務解析費率時不必重新抓取。
            "fare_rules_json": (
                json.dumps(raw["FareInfo"], ensure_ascii=False, separators=(",", ":"))
                if raw.get("FareInfo") else None
            ),
        })
    return lots


def parse_dynamic(payload, captured_at):
    """保留非負汽車剩餘格數；總格數合理性在兩份資料合併後再次檢查。"""
    source_time = parse_source_time(payload["data"]["UPDATETIME"])
    return [{
        "lot_id": str(raw["id"]),
        "available_spaces": int(raw["availablecar"]),
        "source_updated_at": source_time,
        "captured_at": captured_at,
    } for raw in payload["data"]["park"]
      if raw.get("availablecar") is not None and int(raw["availablecar"]) >= 0]


def fetch_json(url, timeout=15):
    """下載 JSON 並在 HTTP 或 JSON 錯誤時直接拋出例外。"""
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def collect_once(timeout=15):
    """先完整下載與清洗兩份資料，再以單一交易寫入，避免半套快照。"""
    static_payload = fetch_json(STATIC_URL, timeout=timeout)
    dynamic_payload = fetch_json(DYNAMIC_URL, timeout=timeout)
    captured_at = datetime.now(timezone.utc)
    raw_snapshots = parse_dynamic(dynamic_payload, captured_at)
    # 即使官方回傳 -9 等狀態，該場站仍屬於支援即時資料，只是不參與本次計算。
    realtime_ids = {str(row["id"]) for row in dynamic_payload["data"]["park"]}
    lots = parse_static(static_payload, realtime_ids)
    totals = {row["lot_id"]: row["total_spaces"] for row in lots}
    snapshots = [row for row in raw_snapshots
                 if clean_available(totals.get(row["lot_id"]), row["available_spaces"]) is not None]
    connection = get_connection()
    try:
        upsert_parking_lots(connection, lots)
        inserted = insert_snapshots(connection, snapshots)
        connection.commit()
        return {"lots": len(lots), "snapshots": inserted}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="蒐集一次臺北市停車資料")
    parser.add_argument("--once", action="store_true", help="執行一次後結束")
    args = parser.parse_args()
    if args.once:
        print(collect_once())
