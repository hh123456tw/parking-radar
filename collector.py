"""下載雙北官方停車資料，每個來源以各自交易獨立保存。"""

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import requests

from analysis import clean_available
from config import Config
from database import (
    fetch_source_lot_state, get_connection, insert_snapshots,
    update_static_fetched_at, upsert_parking_lots,
)
import new_taipei_source
from parking_metadata import infer_official_facility_type

STATIC_URL = "https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_alldesc.json"
DYNAMIC_URL = "https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_allavailable.json"
TAIPEI_TZ = ZoneInfo("Asia/Taipei")

logger = logging.getLogger(__name__)


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
        facility_type, facility_source = infer_official_facility_type(
            raw.get("name", ""), raw.get("summary", ""))
        lots.append({
            "lot_id": str(raw["id"]), "lot_name": raw.get("name", "未命名停車場"),
            "district": raw.get("area", "未知"), "address": raw.get("address", ""),
            "city": "臺北市", "source": "taipei", "source_lot_id": str(raw["id"]),
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
            # 官方名稱與說明的明確關鍵字先寫入，之後由每月同步任務依優先序整理。
            "facility_type": facility_type,
            "facility_source": facility_source,
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


def _collect_taipei(timeout):
    """下載並清洗臺北靜態與動態資料；回傳 (lots, snapshots, static_fetched_at=None)。"""
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
    return lots, snapshots, None


def fetch_new_taipei_static(timeout=15, dynamic_rows=None):
    """下載並解析新北靜態資料集；回傳 (lots, fetched_at)。"""
    rows = new_taipei_source.fetch_pages(
        new_taipei_source.STATIC_DATASET_ID, timeout)
    captured_at = datetime.now(timezone.utc)
    deduped, _duplicates = new_taipei_source.deduplicate_static(rows)
    realtime_ids = {
        str(row["ID"]) for row in dynamic_rows or [] if row.get("ID") is not None}
    lots = new_taipei_source.parse_static(
        list(deduped.values()), realtime_ids, captured_at)
    return lots, captured_at


def _collect_new_taipei(connection, timeout):
    """先抓動態，再依靜態標記決定是否重抓靜態；回傳 (lots, snapshots, fetched_at)。"""
    dynamic_rows = new_taipei_source.fetch_pages(
        new_taipei_source.DYNAMIC_DATASET_ID, timeout)
    captured_at = datetime.now(timezone.utc)
    raw_snapshots = new_taipei_source.parse_dynamic(dynamic_rows, captured_at)
    state = fetch_source_lot_state(connection, "new_taipei")
    totals = state["totals"]
    latest = state["latest_updated_at"]
    if latest is not None and latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    lots = []
    static_fetched_at = None
    if not totals or latest is None or captured_at - latest >= timedelta(hours=24):
        lots, static_fetched_at = fetch_new_taipei_static(timeout, dynamic_rows)
        totals = {row["lot_id"]: row["total_spaces"] for row in lots}
    snapshots = [row for row in raw_snapshots
                 if clean_available(totals.get(row["lot_id"]), row["available_spaces"]) is not None]
    return lots, snapshots, static_fetched_at


def collect_source(source, timeout=15):
    """以來源自己的連線完成下載、驗證與單一交易寫入。"""
    connection = get_connection()
    try:
        if source == "taipei":
            lots, snapshots, static_fetched_at = _collect_taipei(timeout)
        elif source == "new_taipei":
            lots, snapshots, static_fetched_at = _collect_new_taipei(
                connection, timeout)
        else:
            raise ValueError(f"unknown source: {source}")
        if lots:
            upsert_parking_lots(connection, lots)
        # 靜態抓取標記只在成功抓取靜態時寫入，動態-only 週期不得改寫。
        if static_fetched_at is not None and lots:
            update_static_fetched_at(
                connection, [row["lot_id"] for row in lots], static_fetched_at)
        inserted = insert_snapshots(connection, snapshots) if snapshots else 0
        connection.commit()
        return {"lots": len(lots), "snapshots": inserted}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def collect_once(timeout=15, new_taipei_enabled=None):
    """逐來源獨立交易；單一來源失敗不影響其他來源已完成的 commit。"""
    enabled = (
        Config.NEW_TAIPEI_ENABLED if new_taipei_enabled is None
        else new_taipei_enabled)
    sources = ["taipei"] + (["new_taipei"] if enabled else [])
    results = {}
    for source in sources:
        try:
            results[source] = {"status": "ok", **collect_source(source, timeout)}
        except Exception as exc:
            logger.exception("collector_source_failed source=%s", source)
            results[source] = {"status": "error", "error": type(exc).__name__}
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="蒐集一次雙北停車資料")
    parser.add_argument("--once", action="store_true", help="執行一次後結束")
    args = parser.parse_args()
    if args.once:
        summary = collect_once()
        print(json.dumps(summary, ensure_ascii=False))
        if any(item.get("status") == "error" for item in summary.values()):
            sys.exit(1)
