"""下載臺北市官方停車資料，清洗後以單一交易保存。"""

import argparse
import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from analysis import clean_available
from database import (
    get_connection,
    insert_snapshots,
    upsert_collector_heartbeat,
    upsert_parking_lots,
)
from parking_metadata import infer_official_facility_type
from snapshot_cleanup import SNAPSHOT_RETENTION_DAYS

STATIC_URL = "https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_alldesc.json"
DYNAMIC_URL = "https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_allavailable.json"
# 官方 JSON 自 2026-10-19 起停止更新，同一位置的 XML 仍持續更新，兩者都抓、取較新者。
STATIC_XML_URL = "https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_alldesc.xml"
DYNAMIC_XML_URL = "https://tcgbusfs.blob.core.windows.net/blobtcmsv/TCMSV_allavailable.xml"
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
# XML 欄位為大寫，轉成 JSON 版的鍵名，後續解析不必分兩套。
XML_FIELDS = {
    "ID": "id", "AREA": "area", "NAME": "name", "ADDRESS": "address",
    "TYPE2": "type2", "TOTALCAR": "totalcar", "PAYEX": "payex",
    "SERVICETIME": "serviceTime", "SUMMARY": "summary", "AVAILABLECAR": "availablecar",
}
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


def _to_int(value):
    """官方數值偶爾是空白或 N/A；無法轉成整數時回傳 None，只略過單一場站。"""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


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
            # 依 schema.sql 欄位長度截斷，單一過長官方值不致讓整批交易回滾。
            "lot_id": str(raw["id"]),
            "lot_name": (raw.get("name") or "未命名停車場")[:120],
            "district": (raw.get("area") or "未知")[:20],
            "address": (raw.get("address") or "")[:255],
            "operator_type": (raw.get("type2") or "未標示")[:40],
            # 無法解析的總格數記為 0，後續 clean_available 會排除該場站。
            "total_spaces": _to_int(raw.get("totalcar")) or 0,
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
    snapshots = []
    for raw in payload["data"]["park"]:
        # 單筆格式錯誤只略過該場站，不讓整批快照寫入失敗。
        available = _to_int(raw.get("availablecar"))
        if available is None or available < 0:
            continue
        snapshots.append({
            "lot_id": str(raw["id"]),
            "available_spaces": available,
            "source_updated_at": source_time,
            "captured_at": captured_at,
        })
    return snapshots


def fetch_json(url, timeout=15):
    """下載 JSON 並在 HTTP 或 JSON 錯誤時直接拋出例外。"""
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _xml_park(element):
    """把一個 XML <PARK> 轉成與官方 JSON 相同形狀的字典。"""
    park = {key: element.findtext(tag) for tag, key in XML_FIELDS.items()
            if element.find(tag) is not None}
    entrances = [{child.tag: child.text for child in info}
                 for info in element.findall("EntranceCoord/EntrancecoordInfo")]
    if entrances:
        park["EntranceCoord"] = {"EntrancecoordInfo": entrances}
    # XML 的 FareInfo 是空的，費率規則改放在 MAXInfo/FareRule。
    rules = [{child.tag: child.text for child in rule}
             for rule in element.findall("FareInfo/FareRule") + element.findall("MAXInfo/FareRule")]
    if rules:
        park["FareInfo"] = {"FareRule": rules}
    return park


def fetch_xml(url, timeout=15):
    """下載官方 XML 並轉成與 JSON 相同的 {"data": {"UPDATETIME", "park"}} 結構。"""
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    updated = root.findtext("UPDATETIME")
    if not updated:
        raise ValueError(f"官方 XML 缺少 UPDATETIME：{url}")
    return {"data": {"UPDATETIME": updated,
                     "park": [_xml_park(park) for park in root.findall("PARK")]}}


def fetch_feed(json_url, xml_url, timeout=15):
    """同時取官方 JSON 與 XML，回傳官方時間較新的一份；只有一份成功就用那份，都失敗才拋錯。"""
    payloads, errors = [], []
    for fetch, url in ((fetch_json, json_url), (fetch_xml, xml_url)):
        try:
            payload = fetch(url, timeout=timeout)
            payloads.append((parse_source_time(payload["data"]["UPDATETIME"]), payload))
        except Exception as exc:  # 任一格式壞掉都不應擋住另一格式。
            logger.warning("官方資料下載或解析失敗 %s：%s", url, exc)
            errors.append(exc)
    if not payloads:
        raise RuntimeError(f"官方 JSON 與 XML 都無法取得：{errors}")
    return max(payloads, key=lambda item: item[0])[1]


def collect_once(timeout=15):
    """先完整下載與清洗兩份資料，再以單一交易寫入，避免半套快照。"""
    static_payload = fetch_feed(STATIC_URL, STATIC_XML_URL, timeout=timeout)
    dynamic_payload = fetch_feed(DYNAMIC_URL, DYNAMIC_XML_URL, timeout=timeout)
    captured_at = datetime.now(timezone.utc)
    raw_snapshots = parse_dynamic(dynamic_payload, captured_at)
    # 即使官方回傳 -9 等狀態，該場站仍屬於支援即時資料，只是不參與本次計算。
    realtime_ids = {str(row["id"]) for row in dynamic_payload["data"]["park"]}
    lots = parse_static(static_payload, realtime_ids)
    totals = {row["lot_id"]: row["total_spaces"] for row in lots}
    # 官方時間已超過保留期的快照隔天就會被清理，寫入只會讓凍結資料看似新收集。
    retention_cutoff = captured_at - timedelta(days=SNAPSHOT_RETENTION_DAYS)
    snapshots = [row for row in raw_snapshots
                 if row["source_updated_at"] >= retention_cutoff
                 and clean_available(totals.get(row["lot_id"]),
                                     row["available_spaces"]) is not None]
    connection = get_connection()
    try:
        upsert_parking_lots(connection, lots)
        inserted = insert_snapshots(connection, snapshots)
        upsert_collector_heartbeat(connection, captured_at)
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
