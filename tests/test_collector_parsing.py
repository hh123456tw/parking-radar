"""臺北市官方資料解析測試：固定 JSON fixture，不呼叫真實網路。"""

import json
from datetime import datetime, timezone
from pathlib import Path

import requests

import collector
import new_taipei_source

FIXTURES = Path(__file__).parent / "fixtures"


class JsonResponse:
    """提供 collector.fetch_json 所需的最小 HTTP response。"""

    def __init__(self, payload):
        self.payload = payload
        self.status_checked = False

    def raise_for_status(self):
        self.status_checked = True

    def json(self):
        return self.payload


def load_fixture(name):
    """讀取固定官方格式，避免測試依賴即時資料變化。"""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_static_parser_uses_exact_id_and_valid_wgs84_entrance():
    """靜態資料需保留官方 ID、業者類型、座標與即時支援狀態。"""
    lots = collector.parse_static(load_fixture("taipei_static.json"), {"TPE0001"})

    assert lots[0]["lot_id"] == "TPE0001"
    assert lots[0]["city"] == "臺北市"
    assert lots[0]["source"] == "taipei"
    assert lots[0]["source_lot_id"] == "TPE0001"
    assert lots[0]["operator_type"] == "民營停車場"
    assert lots[0]["latitude"] == 25.0552
    assert lots[0]["longitude"] == 121.5242
    assert lots[0]["supports_realtime"] is True
    assert lots[1]["latitude"] is None
    assert lots[1]["longitude"] is None
    assert lots[1]["supports_realtime"] is False


def test_static_parser_preserves_raw_fare_rules_as_utf8_json():
    """原始 FareInfo 必須完整保存，中文不得被 ASCII 跳脫。"""
    lot = collector.parse_static(
        load_fixture("taipei_static.json"), {"TPE0001"})[0]

    assert json.loads(lot["fare_rules_json"])["FareRule"][0] == {
        "ParkingType": "C", "RateType": "1",
        "ChargeableSTime": "0800", "ChargeableETime": "2200",
        "ParkingRates": "60",
    }
    assert "\\u" not in lot["fare_rules_json"]


def test_static_parser_rejects_malformed_and_out_of_taipei_coordinates():
    """格式錯誤或超出臺北範圍的入口座標不得參與附近推薦。"""
    payload = load_fixture("taipei_static.json")
    malformed = payload["data"]["park"][0]
    malformed["EntranceCoord"] = {"EntrancecoordInfo": [
        {"Xcod": "not-a-number", "Ycod": "121.5"}]}
    out_of_range = dict(malformed, id="TPE-X")
    out_of_range["EntranceCoord"] = {"EntrancecoordInfo": [
        {"Xcod": "23.5", "Ycod": "121.5"}]}
    payload["data"]["park"] = [malformed, out_of_range]

    lots = collector.parse_static(payload, set())

    assert [(lot["latitude"], lot["longitude"]) for lot in lots] == [
        (None, None), (None, None)]


def test_static_parser_derives_facility_type_from_name_and_summary():
    """官方名稱與說明中的明確關鍵字必須寫入 facility_type 與 facility_source。"""
    payload = load_fixture("taipei_static.json")
    payload["data"]["park"][0]["name"] = "市民大道地下停車場"
    payload["data"]["park"][0]["summary"] = "地下四層結構"
    lots = collector.parse_static(payload, {"TPE0001"})

    assert lots[0]["facility_type"] == "underground"
    assert lots[0]["facility_source"] == "official"


def test_static_parser_defaults_facility_to_unknown_without_keywords():
    """名稱與說明皆無關鍵字時，型態維持 unknown，方便後續 OSM 補足。"""
    lots = collector.parse_static(load_fixture("taipei_static.json"), set())

    assert all(lot["facility_type"] == "unknown" for lot in lots)
    assert all(lot["facility_source"] == "unknown" for lot in lots)


def test_dynamic_parser_keeps_nonnegative_values_for_join_validation():
    """動態 parser 先排除負數，超額值留給合併總格數後判斷。"""
    captured = datetime(2026, 8, 3, 10, 1, tzinfo=timezone.utc)
    snapshots = collector.parse_dynamic(load_fixture("taipei_dynamic.json"), captured)

    assert [row["lot_id"] for row in snapshots] == ["TPE0001", "TPE0003"]
    assert snapshots[0]["available_spaces"] == 8
    assert snapshots[1]["available_spaces"] == 999
    assert snapshots[0]["source_updated_at"] == datetime(
        2026, 8, 3, 10, 0, tzinfo=timezone.utc)


def test_source_time_without_offset_is_interpreted_as_taipei_time():
    """官方時間缺少 offset 時，依資料來源所在地補 Asia/Taipei。"""
    assert collector.parse_source_time("2026-08-03T18:00:00") == datetime(
        2026, 8, 3, 10, 0, tzinfo=timezone.utc)


def test_source_time_accepts_current_taipei_cst_format():
    """官方 API 的英文 CST 日期格式應視為臺北時間並轉成 UTC。"""
    assert collector.parse_source_time("Tue Aug 04 12:04:00 CST 2026") == datetime(
        2026, 8, 4, 4, 4, tzinfo=timezone.utc)


def test_fetch_json_uses_timeout_and_checks_http_status(monkeypatch):
    """下載官方資料必須設定 timeout 並在解析前檢查 HTTP 狀態。"""
    payload = {"data": {"park": []}}
    response = JsonResponse(payload)
    captured = {}

    def fake_get(url, timeout):
        captured.update(url=url, timeout=timeout)
        return response

    monkeypatch.setattr(collector.requests, "get", fake_get)

    assert collector.fetch_json("https://example.test/data.json", timeout=7) == payload
    assert captured == {"url": "https://example.test/data.json", "timeout": 7}
    assert response.status_checked is True


def test_fetch_new_taipei_static_parses_with_dynamic_realtime_ids(monkeypatch):
    """collector 的新北靜態抓取必須沿用官方解析器與動態即時支援 ID。"""
    static_rows = load_fixture("new_taipei_static.json")
    dynamic_rows = load_fixture("new_taipei_dynamic.json")
    monkeypatch.setattr(
        new_taipei_source, "fetch_pages",
        lambda dataset_id, timeout, http_get=requests.get: static_rows)

    lots, fetched_at = collector.fetch_new_taipei_static(
        timeout=3, dynamic_rows=dynamic_rows)

    assert [lot["lot_id"] for lot in lots] == ["NTP:010056", "NTP:060040"]
    assert lots[0]["supports_realtime"] is True
    assert fetched_at.tzinfo is not None
