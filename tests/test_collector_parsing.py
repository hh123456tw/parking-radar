"""臺北市官方資料解析測試：固定 JSON fixture，不呼叫真實網路。"""

import json
from datetime import datetime, timezone
from pathlib import Path

import collector

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
    assert lots[0]["operator_type"] == "民營停車場"
    assert lots[0]["latitude"] == 25.0552
    assert lots[0]["longitude"] == 121.5242
    assert lots[0]["supports_realtime"] is True
    assert lots[1]["latitude"] is None
    assert lots[1]["longitude"] is None
    assert lots[1]["supports_realtime"] is False


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
